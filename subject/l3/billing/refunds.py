"""Refunds and credit notes for paid invoices."""

from .context import Context
from .errors import InvalidStateError, ValidationError
from .events import emit
from .ledger import add_entry
from .models import Cents, CreditNote, InvoiceId
from .payments import successful_payments
from .serialization import payment_json


def _credit_note(ctx: Context, invoice_id: InvoiceId, amount: Cents, reason: str, refunded: bool) -> CreditNote:
    invoice = ctx.store.require_invoice(invoice_id)
    note: CreditNote = {
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


def credited_total(ctx: Context, invoice_id: InvoiceId) -> Cents:
    return Cents(sum(note["amount"] for note in ctx.store.credit_notes_for(invoice_id)))


def refund_invoice(ctx: Context, invoice_id: InvoiceId, reason: str = "requested_by_customer") -> CreditNote:
    """Refund everything that was paid on an invoice back to the original payment method."""
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] != "paid":
        raise InvalidStateError("only paid invoices can be refunded")
    if credited_total(ctx, invoice_id) > 0:
        raise InvalidStateError("invoice has already been refunded or credited")
    payments = successful_payments(ctx, invoice["id"])
    refunded = Cents(0)
    for payment in payments:
        remaining = Cents(payment["amount"] - payment["refunded_amount"])
        if remaining <= 0 or payment["gateway_ref"] is None:
            continue
        result = ctx.gateway.refund(payment["gateway_ref"], remaining)
        if not result["ok"]:
            raise InvalidStateError(f"gateway refused refund for payment {payment['id']}")
        payment["refunded_amount"] = Cents(payment["refunded_amount"] + remaining)
        payment["status"] = "refunded"
        refunded = Cents(refunded + remaining)
        emit(ctx, "payment.refunded", {"payment": payment_json(payment)})
    if invoice["credit_applied"] > 0:
        add_entry(ctx, invoice["customer_id"], invoice["credit_applied"], "refund", invoice["id"])
    if refunded == 0 and invoice["credit_applied"] == 0:
        raise InvalidStateError("invoice has no successful payment to refund")
    return _credit_note(ctx, invoice_id, Cents(refunded + invoice["credit_applied"]), reason, True)


def credit_invoice(ctx: Context, invoice_id: InvoiceId, amount: Cents, reason: str) -> CreditNote:
    """Issue account credit against a paid invoice instead of returning money."""
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] != "paid":
        raise InvalidStateError("only paid invoices can be credited")
    if amount <= 0:
        raise ValidationError("credit amount must be positive")
    available = invoice["total"] - credited_total(ctx, invoice_id)
    if amount > available:
        raise ValidationError(f"cannot credit more than {available} on this invoice")
    add_entry(ctx, invoice["customer_id"], amount, "credit_note", invoice_id)
    return _credit_note(ctx, invoice_id, amount, reason, False)
