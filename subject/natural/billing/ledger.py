"""Customer credit balance ledger.

Entries are signed amounts in minor units: positive entries give the customer credit,
negative entries consume it.
"""

from .errors import ValidationError


def add_entry(ctx, customer_id, amount, reason, invoice_id=None):
    """Append a signed balance change for the customer and return the entry."""
    ctx.store.require_customer(customer_id)
    entry = {
        "id": ctx.ids.ledger_entry(),
        "customer_id": customer_id,
        "amount": amount,
        "reason": reason,
        "invoice_id": invoice_id,
        "created_at": ctx.clock.now(),
    }
    ctx.store.ledger.append(entry)
    return entry


def balance(ctx, customer_id):
    """Credit available to the customer. Positive means we owe them."""
    return sum(entry["amount"] for entry in ctx.store.ledger_for(customer_id))


def grant_credit(ctx, customer_id, amount):
    """Give the customer `amount` minor units of credit; raises ValidationError unless positive."""
    if amount <= 0:
        raise ValidationError("credit grants must be positive")
    return add_entry(ctx, customer_id, amount, "manual")


def apply_credit(ctx, invoice):
    """Use available credit against the invoice; returns how much was used."""
    available_credit = balance(ctx, invoice["customer_id"])
    if available_credit <= 0 or invoice["amount_due"] <= 0:
        return 0
    credit_used = min(available_credit, invoice["amount_due"])
    add_entry(ctx, invoice["customer_id"], -credit_used, "invoice_applied", invoice["id"])
    invoice["credit_applied"] = invoice["credit_applied"] + credit_used
    invoice["amount_due"] = invoice["amount_due"] - credit_used
    return credit_used


def restore_credit(ctx, invoice, reason):
    """Give back any credit that was applied to the invoice; returns the amount restored."""
    restored_amount = invoice["credit_applied"]
    if restored_amount > 0:
        add_entry(ctx, invoice["customer_id"], restored_amount, reason, invoice["id"])
        invoice["credit_applied"] = 0
    return restored_amount
