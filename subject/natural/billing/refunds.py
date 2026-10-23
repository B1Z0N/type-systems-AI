"""Refunds and credit notes for paid invoices."""

from .errors import InvalidStateError, ValidationError
from .events import emit
from .ledger import add_entry
from .payments import successful_payments
from .serialization import payment_json


def _create_credit_note(ctx, invoice_id, amount, reason, refunded):
    """Store a credit note and emit "credit_note.created".

    `refunded` is true when money went back to the customer and false when it became
    account credit.
    """
    invoice = ctx.store.require_invoice(invoice_id)
    note = {
        "id": ctx.ids.credit_note(),
        "invoice_id": invoice_id,
        "customer_id": invoice["customer_id"],
        "amount": amount,
        "reason": reason,
        "refunded": refunded,
        "created_at": ctx.clock.now(),
    }
    ctx.store.credit_notes[note["id"]] = note
    emit(
        ctx,
        "credit_note.created",
        {"credit_note_id": note["id"], "invoice_id": invoice_id, "amount": amount, "refunded": refunded},
    )
    return note


def credited_total(ctx, invoice_id):
    """Sum of all credit notes issued against the invoice, in minor units."""
    return sum(note["amount"] for note in ctx.store.credit_notes_for(invoice_id))


def refund_invoice(ctx, invoice_id, reason="requested_by_customer"):
    """Refund everything that was paid on an invoice back to the original payment method.

    Account credit that was applied to the invoice is returned to the ledger rather than
    the card. Returns the credit note covering both. Raises InvalidStateError if the
    invoice is not paid, was already credited, has nothing to refund, or the gateway
    refuses a refund (payments refunded before that point stay refunded).
    """
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] != "paid":
        raise InvalidStateError("only paid invoices can be refunded")
    if credited_total(ctx, invoice_id) > 0:
        raise InvalidStateError("invoice has already been refunded or credited")
    payments = successful_payments(ctx, invoice["id"])
    refunded_total = 0
    for payment in payments:
        refundable_amount = payment["amount"] - payment["refunded_amount"]
        if refundable_amount <= 0 or payment["gateway_ref"] is None:
            continue
        result = ctx.gateway.refund(payment["gateway_ref"], refundable_amount)
        if not result["ok"]:
            raise InvalidStateError(f"gateway refused refund for payment {payment['id']}")
        payment["refunded_amount"] = payment["refunded_amount"] + refundable_amount
        payment["status"] = "refunded"
        refunded_total = refunded_total + refundable_amount
        emit(ctx, "payment.refunded", {"payment": payment_json(payment)})
    if invoice["credit_applied"] > 0:
        add_entry(ctx, invoice["customer_id"], invoice["credit_applied"], "refund", invoice["id"])
    if refunded_total == 0 and invoice["credit_applied"] == 0:
        raise InvalidStateError("invoice has no successful payment to refund")
    return _create_credit_note(ctx, invoice_id, refunded_total + invoice["credit_applied"], reason, True)


def credit_invoice(ctx, invoice_id, amount, reason):
    """Issue account credit against a paid invoice instead of returning money.

    `amount` is in minor units and, together with earlier credit notes, may not exceed
    the invoice total. Raises InvalidStateError unless the invoice is paid and
    ValidationError for a non-positive or excessive amount.
    """
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] != "paid":
        raise InvalidStateError("only paid invoices can be credited")
    if amount <= 0:
        raise ValidationError("credit amount must be positive")
    creditable_amount = invoice["total"] - credited_total(ctx, invoice_id)
    if amount > creditable_amount:
        raise ValidationError(f"cannot credit more than {creditable_amount} on this invoice")
    add_entry(ctx, invoice["customer_id"], amount, "credit_note", invoice_id)
    return _create_credit_note(ctx, invoice_id, amount, reason, False)
