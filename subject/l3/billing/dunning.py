"""Retrying failed payments and escalating when retries run out."""

from typing import Final

from .context import Context
from .events import emit
from .invoices import mark_uncollectible
from .models import SECONDS_PER_DAY, DunningPolicy, EpochSeconds, Invoice, InvoiceId
from .serialization import invoice_json
from .subscriptions import transition

DEFAULT_POLICY: Final[DunningPolicy] = {"retry_offsets_days": [1, 3, 7], "final_action": "mark_unpaid"}


def record_failure(ctx: Context, invoice: Invoice, failure_code: str) -> None:
    """Count a failed attempt and either schedule a retry or apply the final action."""
    invoice["attempt_count"] += 1
    policy = ctx.dunning_policy
    offsets = policy["retry_offsets_days"]
    emit(
        ctx,
        "invoice.payment_failed",
        {"invoice": invoice_json(invoice), "failure_code": failure_code, "attempt": invoice["attempt_count"]},
    )
    subscription = (
        ctx.store.get_subscription(invoice["subscription_id"]) if invoice["subscription_id"] is not None else None
    )
    if invoice["attempt_count"] <= len(offsets):
        base = invoice["finalized_at"] if invoice["finalized_at"] is not None else ctx.clock.now()
        invoice["next_attempt_at"] = EpochSeconds(base + offsets[invoice["attempt_count"] - 1] * SECONDS_PER_DAY)
        if subscription is not None and subscription["status"] in ("active", "trialing"):
            transition(ctx, subscription, "past_due")
        return
    invoice["next_attempt_at"] = None
    if subscription is None or subscription["status"] == "canceled":
        mark_uncollectible(ctx, invoice["id"])
        return
    if subscription["status"] in ("paused", "unpaid"):
        return
    action = policy["final_action"]
    if action == "cancel":
        mark_uncollectible(ctx, invoice["id"])
        transition(ctx, subscription, "canceled")
    elif action == "pause":
        transition(ctx, subscription, "paused")
    else:
        transition(ctx, subscription, "unpaid")


def due_for_retry(ctx: Context, now: EpochSeconds) -> list[InvoiceId]:
    due = [
        invoice
        for invoice in ctx.store.invoices.values()
        if invoice["status"] == "open" and invoice["next_attempt_at"] is not None and invoice["next_attempt_at"] <= now
    ]
    return [invoice["id"] for invoice in sorted(due, key=lambda inv: (inv["next_attempt_at"] or 0, inv["id"]))]


def recover(ctx: Context, invoice: Invoice) -> None:
    """Return a past-due or unpaid subscription to active once its invoice is paid."""
    if invoice["subscription_id"] is None:
        return
    subscription = ctx.store.get_subscription(invoice["subscription_id"])
    if subscription is None or subscription["status"] not in ("past_due", "unpaid"):
        return
    outstanding = [
        inv
        for inv in ctx.store.invoices_for_subscription(subscription["id"])
        if inv["status"] == "open" and inv["id"] != invoice["id"]
    ]
    if not outstanding:
        transition(ctx, subscription, "active")
