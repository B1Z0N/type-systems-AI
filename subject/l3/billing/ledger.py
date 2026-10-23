"""Customer credit balance ledger."""

from .context import Context
from .errors import ValidationError
from .models import Cents, CustomerId, Invoice, InvoiceId, LedgerEntry, LedgerReason


def add_entry(
    ctx: Context,
    customer_id: CustomerId,
    amount: Cents,
    reason: LedgerReason,
    invoice_id: InvoiceId | None = None,
) -> LedgerEntry:
    ctx.store.require_customer(customer_id)
    entry: LedgerEntry = {
        "id": ctx.ids.ledger_entry(),
        "customer_id": customer_id,
        "amount": amount,
        "reason": reason,
        "invoice_id": invoice_id,
        "created_at": ctx.clock.now(),
    }
    ctx.store.ledger.append(entry)
    return entry


def balance(ctx: Context, customer_id: CustomerId) -> Cents:
    """Credit available to the customer. Positive means we owe them."""
    return Cents(sum(entry["amount"] for entry in ctx.store.ledger_for(customer_id)))


def grant_credit(ctx: Context, customer_id: CustomerId, amount: Cents) -> LedgerEntry:
    if amount <= 0:
        raise ValidationError("credit grants must be positive")
    return add_entry(ctx, customer_id, amount, "manual")


def apply_credit(ctx: Context, invoice: Invoice) -> Cents:
    """Use available credit against the invoice; returns how much was used."""
    available = balance(ctx, invoice["customer_id"])
    if available <= 0 or invoice["amount_due"] <= 0:
        return Cents(0)
    used = Cents(min(available, invoice["amount_due"]))
    add_entry(ctx, invoice["customer_id"], Cents(-used), "invoice_applied", invoice["id"])
    invoice["credit_applied"] = Cents(invoice["credit_applied"] + used)
    invoice["amount_due"] = Cents(invoice["amount_due"] - used)
    return used


def restore_credit(ctx: Context, invoice: Invoice, reason: LedgerReason) -> Cents:
    restored = invoice["credit_applied"]
    if restored > 0:
        add_entry(ctx, invoice["customer_id"], restored, reason, invoice["id"])
        invoice["credit_applied"] = Cents(0)
    return restored
