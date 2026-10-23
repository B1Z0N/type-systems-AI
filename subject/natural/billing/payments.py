"""Charging invoices through the payment gateway."""

from .customers import default_payment_method, is_expired
from .dunning import record_failure, recover
from .errors import InvalidStateError
from .invoices import mark_paid


def collect(ctx, invoice_id):
    """Attempt to charge the invoice's amount due; returns the payment if one was made.

    Zero-amount invoices are simply marked paid. A missing or expired payment method
    counts as a failed attempt for dunning without creating a payment record. Raises
    InvalidStateError unless the invoice is open.
    """
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
    # One idempotency key per attempt: retries are new charges, replays of the same attempt are not.
    idempotency_key = f"{invoice['id']}:{invoice['attempt_count']}"
    result = ctx.gateway.charge(method, invoice["amount_due"], invoice["currency"], idempotency_key)
    payment = {
        "id": ctx.ids.payment(),
        "invoice_id": invoice["id"],
        "customer_id": invoice["customer_id"],
        "method_id": method["id"],
        "amount": invoice["amount_due"],
        "currency": invoice["currency"],
        "status": "succeeded" if result["ok"] else "failed",
        "refunded_amount": 0,
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


def successful_payments(ctx, invoice_id):
    """Payments on the invoice that captured money (including partially refunded ones)."""
    return [
        payment
        for payment in ctx.store.payments_for_invoice(invoice_id)
        if payment["status"] in ("succeeded", "partially_refunded")
    ]
