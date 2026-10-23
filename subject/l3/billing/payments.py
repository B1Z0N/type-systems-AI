"""Charging invoices through the payment gateway."""

from .context import Context
from .customers import default_payment_method, is_expired
from .dunning import record_failure, recover
from .errors import InvalidStateError
from .invoices import mark_paid
from .models import Cents, InvoiceId, Payment


def collect(ctx: Context, invoice_id: InvoiceId) -> Payment | None:
    """Attempt to charge the invoice's amount due; returns the payment if one was made."""
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] != "open":
        raise InvalidStateError(f"cannot collect a {invoice['status']} invoice")
    if invoice["amount_due"] == 0:
        mark_paid(ctx, invoice, invoice["amount_due"])
        return None
    method = default_payment_method(ctx, invoice["customer_id"])
    if method is None:
        record_failure(ctx, invoice, "no_payment_method")
        return None
    now = ctx.clock.now()
    if is_expired(method, now):
        record_failure(ctx, invoice, "expired_card")
        return None
    result = ctx.gateway.charge(
        method, invoice["amount_due"], invoice["currency"], f"{invoice['id']}:{invoice['attempt_count']}"
    )
    payment: Payment = {
        "id": ctx.ids.payment(),
        "invoice_id": invoice["id"],
        "customer_id": invoice["customer_id"],
        "method_id": method["id"],
        "amount": invoice["amount_due"],
        "currency": invoice["currency"],
        "status": "succeeded" if result["ok"] else "failed",
        "refunded_amount": Cents(0),
        "gateway_ref": result["gateway_ref"],
        "failure_code": result["failure_code"],
        "created_at": now,
    }
    ctx.store.payments[payment["id"]] = payment
    if result["ok"]:
        mark_paid(ctx, invoice, payment["amount"])
        recover(ctx, invoice)
    else:
        record_failure(ctx, invoice, result["failure_code"] or "unknown")
    return payment


def successful_payments(ctx: Context, invoice_id: InvoiceId) -> list[Payment]:
    return [
        payment
        for payment in ctx.store.payments_for_invoice(invoice_id)
        if payment["status"] in ("succeeded", "partially_refunded")
    ]
