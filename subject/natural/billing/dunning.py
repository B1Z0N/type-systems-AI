"""Retrying failed payments and escalating when retries run out."""

from .events import emit
from .invoices import mark_uncollectible
from .models import SECONDS_PER_DAY
from .serialization import invoice_json
from .subscriptions import transition

# Retry offsets count days from the invoice's finalization, not from the previous attempt.
# final_action is "cancel", "pause" or anything else (normally "mark_unpaid").
DEFAULT_POLICY = {"retry_offsets_days": [1, 3, 7], "final_action": "mark_unpaid"}


def record_failure(ctx, invoice, failure_code):
    """Count a failed attempt and either schedule a retry or apply the final action.

    Emits "invoice.payment_failed". While retries remain, the next attempt is scheduled
    and an active or trialing subscription becomes past_due. Once they run out, the
    policy's final action applies: invoices without a live subscription become
    uncollectible, and paused or unpaid subscriptions are left alone.
    """
    invoice["attempt_count"] += 1
    policy = ctx.dunning_policy
    retry_offsets_days = policy["retry_offsets_days"]
    emit(
        ctx,
        "invoice.payment_failed",
        {"invoice": invoice_json(invoice), "failure_code": failure_code, "attempt": invoice["attempt_count"]},
    )
    subscription = (
        ctx.store.get_subscription(invoice["subscription_id"]) if invoice["subscription_id"] is not None else None
    )
    if invoice["attempt_count"] <= len(retry_offsets_days):
        retry_base = invoice["finalized_at"] if invoice["finalized_at"] is not None else ctx.clock.now()
        retry_offset_days = retry_offsets_days[invoice["attempt_count"] - 1]
        invoice["next_attempt_at"] = retry_base + retry_offset_days * SECONDS_PER_DAY
        if subscription is not None and subscription["status"] in ("active", "trialing"):
            transition(ctx, subscription, "past_due")
        return
    invoice["next_attempt_at"] = None
    if subscription is None or subscription["status"] == "canceled":
        mark_uncollectible(ctx, invoice["id"])
        return
    if subscription["status"] in ("paused", "unpaid"):
        return
    final_action = policy["final_action"]
    if final_action == "cancel":
        mark_uncollectible(ctx, invoice["id"])
        transition(ctx, subscription, "canceled")
    elif final_action == "pause":
        transition(ctx, subscription, "paused")
    else:
        transition(ctx, subscription, "unpaid")


def due_for_retry(ctx, now):
    """Ids of open invoices whose next payment attempt is due at epoch second `now`.

    Ordered by attempt time, then invoice id.
    """
    due_invoices = [
        invoice
        for invoice in ctx.store.invoices.values()
        if invoice["status"] == "open" and invoice["next_attempt_at"] is not None and invoice["next_attempt_at"] <= now
    ]
    due_invoices.sort(key=lambda invoice: (invoice["next_attempt_at"], invoice["id"]))
    return [invoice["id"] for invoice in due_invoices]


def recover(ctx, invoice):
    """Return a past-due or unpaid subscription to active once its invoice is paid.

    The subscription stays as it is while any of its other invoices is still open.
    """
    if invoice["subscription_id"] is None:
        return
    subscription = ctx.store.get_subscription(invoice["subscription_id"])
    if subscription is None or subscription["status"] not in ("past_due", "unpaid"):
        return
    other_open_invoices = [
        other
        for other in ctx.store.invoices_for_subscription(subscription["id"])
        if other["status"] == "open" and other["id"] != invoice["id"]
    ]
    if not other_open_invoices:
        transition(ctx, subscription, "active")
