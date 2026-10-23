"""Subscription lifecycle and state transitions.

Period boundaries (`current_period_start`, `current_period_end`, `trial_end`) are epoch
seconds; the current period is [start, end).
"""

from .clock import add_interval, civil
from .discounts import is_redeemable, redeem
from .errors import CurrencyMismatchError, InvalidStateError, ValidationError
from .events import emit
from .invoices import add_pending_item
from .models import SECONDS_PER_DAY
from .proration import proration_lines
from .serialization import subscription_json

ALLOWED_TRANSITIONS = {
    "trialing": frozenset({"active", "past_due", "canceled", "paused"}),
    "active": frozenset({"past_due", "paused", "canceled"}),
    "past_due": frozenset({"active", "unpaid", "canceled", "paused"}),
    "unpaid": frozenset({"active", "canceled"}),
    "paused": frozenset({"active", "canceled"}),
    "canceled": frozenset(),
}

# Statuses whose periods keep renewing and generating invoices.
BILLABLE_STATUSES = frozenset({"trialing", "active", "past_due"})


def transition(ctx, subscription, status):
    """Move the subscription to `status` and emit an event; a no-op if already there.

    Emits "subscription.canceled" when canceling and "subscription.updated" otherwise.
    Raises InvalidStateError if ALLOWED_TRANSITIONS forbids the move.
    """
    previous_status = subscription["status"]
    if status == previous_status:
        return
    if status not in ALLOWED_TRANSITIONS[previous_status]:
        raise InvalidStateError(f"subscription cannot go from {previous_status} to {status}")
    subscription["status"] = status
    now = ctx.clock.now()
    event_type = "subscription.updated"
    if status == "canceled":
        subscription["canceled_at"] = now
        event_type = "subscription.canceled"
    if status == "paused":
        subscription["paused_at"] = now
    elif previous_status == "paused":
        subscription["paused_at"] = None
    emit(ctx, event_type, {"subscription": subscription_json(subscription), "previous_status": previous_status})


def _resolve_items(ctx, items):
    """Validate (price_id, quantity) pairs; return (item dicts, price records) in input order.

    Metered items may have quantity 0, licensed items need a positive quantity, and all
    prices must be active and share one billing interval and currency.
    """
    if not items:
        raise ValidationError("a subscription needs at least one item")
    seen_price_ids = set()
    resolved_items = []
    prices = []
    for price_id, quantity in items:
        if price_id in seen_price_ids:
            raise ValidationError(f"duplicate price in subscription: {price_id}")
        seen_price_ids.add(price_id)
        price = ctx.store.require_price(price_id)
        if not price["active"]:
            raise ValidationError(f"price {price_id} is archived")
        if quantity < 0 or (quantity == 0 and price["model"] != "metered"):
            raise ValidationError("licensed items need a positive quantity")
        resolved_items.append({"price_id": price_id, "quantity": quantity})
        prices.append(price)
    first_price = prices[0]
    for price in prices[1:]:
        if (price["interval"], price["interval_count"]) != (first_price["interval"], first_price["interval_count"]):
            raise ValidationError("all prices in a subscription must share a billing interval")
        if price["currency"] != first_price["currency"]:
            raise CurrencyMismatchError("all prices in a subscription must share a currency")
    return resolved_items, prices


def billing_interval(ctx, subscription):
    """(interval, interval_count) shared by all of the subscription's prices."""
    price = ctx.store.require_price(subscription["items"][0]["price_id"])
    return price["interval"], price["interval_count"]


def next_period_end(ctx, subscription, start):
    """End of a billing period beginning at epoch second `start`, honouring the anchor day."""
    interval, interval_count = billing_interval(ctx, subscription)
    return add_interval(start, interval, interval_count, subscription["anchor_day"])


def create(ctx, customer_id, items, *, trial_days=None, coupon_id=None):
    """Create and store a subscription and emit "subscription.created".

    `items` is a list of (price_id, quantity). `trial_days` defaults to the longest
    trial of the subscribed plans; with a trial the subscription starts "trialing" and
    its first period ends when the trial does. The billing anchor is today's day of
    month. An optional coupon is redeemed immediately.

    Raises CurrencyMismatchError if the prices are not in the customer's currency,
    InvalidStateError if the coupon cannot be redeemed, and ValidationError for bad items.
    """
    customer = ctx.store.require_customer(customer_id)
    resolved_items, prices = _resolve_items(ctx, items)
    if prices[0]["currency"] != customer["currency"]:
        raise CurrencyMismatchError("subscription currency must match the customer currency")
    if coupon_id is not None and not is_redeemable(ctx.store.require_coupon(coupon_id), ctx.clock.now()):
        raise InvalidStateError(f"coupon {coupon_id} can no longer be redeemed")
    if trial_days is None:
        trial_days = max(ctx.store.require_plan(price["plan_id"])["trial_days"] for price in prices)
    now = ctx.clock.now()
    subscription = {
        "id": ctx.ids.subscription(),
        "customer_id": customer_id,
        "status": "trialing" if trial_days > 0 else "active",
        "items": resolved_items,
        "current_period_start": now,
        # Placeholder: next_period_end needs the stored items, so it is filled in below.
        "current_period_end": now,
        "anchor_day": civil(now).day,
        "trial_end": now + trial_days * SECONDS_PER_DAY if trial_days > 0 else None,
        "cancel_at_period_end": False,
        "canceled_at": None,
        "paused_at": None,
        "coupon_id": None,
        "coupon_periods_used": 0,
        "created_at": now,
    }
    trial_end = subscription["trial_end"]
    subscription["current_period_end"] = trial_end if trial_end is not None else next_period_end(ctx, subscription, now)
    ctx.store.subscriptions[subscription["id"]] = subscription
    if coupon_id is not None:
        redeem(ctx, subscription["id"], coupon_id)
    emit(ctx, "subscription.created", {"subscription": subscription_json(subscription)})
    return subscription


def change_items(ctx, subscription_id, items, *, prorate=True):
    """Replace the subscription's items, keeping its billing interval and period.

    When `prorate` is true and the subscription is not trialing, credits for the old
    items and charges for the new ones are queued for the next invoice. Emits
    "subscription.updated". Raises InvalidStateError for paused, unpaid or canceled
    subscriptions, CurrencyMismatchError or ValidationError for incompatible items.
    """
    subscription = ctx.store.require_subscription(subscription_id)
    if subscription["status"] not in ("active", "trialing", "past_due"):
        raise InvalidStateError(f"cannot change a {subscription['status']} subscription")
    resolved_items, prices = _resolve_items(ctx, items)
    customer = ctx.store.require_customer(subscription["customer_id"])
    if prices[0]["currency"] != customer["currency"]:
        raise CurrencyMismatchError("new prices must use the customer currency")
    current_interval = billing_interval(ctx, subscription)
    if (prices[0]["interval"], prices[0]["interval_count"]) != current_interval:
        raise ValidationError("changing the billing interval is not supported")
    old_items = subscription["items"]
    if prorate and subscription["status"] != "trialing":
        for line in proration_lines(ctx, subscription, old_items, resolved_items, ctx.clock.now()):
            add_pending_item(ctx, subscription, line)
    subscription["items"] = resolved_items
    emit(
        ctx,
        "subscription.updated",
        {"subscription": subscription_json(subscription), "previous_status": subscription["status"]},
    )
    return subscription


def cancel(ctx, subscription_id, *, at_period_end=True):
    """Cancel now, or flag the subscription to cancel when its current period ends.

    Scheduling only applies to billable subscriptions; paused or unpaid ones are always
    canceled immediately, which also drops their pending invoice items. Raises
    InvalidStateError if the subscription is already canceled.
    """
    subscription = ctx.store.require_subscription(subscription_id)
    if subscription["status"] == "canceled":
        raise InvalidStateError("subscription is already canceled")
    if at_period_end and subscription["status"] in ("active", "trialing", "past_due"):
        subscription["cancel_at_period_end"] = True
        emit(
            ctx,
            "subscription.updated",
            {"subscription": subscription_json(subscription), "previous_status": subscription["status"]},
        )
        return subscription
    transition(ctx, subscription, "canceled")
    ctx.store.pending_items.pop(subscription_id, None)
    return subscription


def reactivate(ctx, subscription_id):
    """Undo a scheduled cancellation; raises InvalidStateError if none is scheduled."""
    subscription = ctx.store.require_subscription(subscription_id)
    if not subscription["cancel_at_period_end"]:
        raise InvalidStateError("subscription is not scheduled for cancellation")
    subscription["cancel_at_period_end"] = False
    emit(
        ctx,
        "subscription.updated",
        {"subscription": subscription_json(subscription), "previous_status": subscription["status"]},
    )
    return subscription


def pause(ctx, subscription_id):
    """Pause the subscription; raises InvalidStateError if its status cannot be paused."""
    subscription = ctx.store.require_subscription(subscription_id)
    transition(ctx, subscription, "paused")
    return subscription


def resume(ctx, subscription_id):
    """Resume a paused subscription with a fresh period starting now.

    The billing anchor moves to today's day of month. Raises InvalidStateError unless
    the subscription is paused.
    """
    subscription = ctx.store.require_subscription(subscription_id)
    if subscription["status"] != "paused":
        raise InvalidStateError("only paused subscriptions can be resumed")
    now = ctx.clock.now()
    transition(ctx, subscription, "active")
    subscription["current_period_start"] = now
    subscription["anchor_day"] = civil(now).day
    subscription["current_period_end"] = next_period_end(ctx, subscription, now)
    return subscription


def advance_period(ctx, subscription):
    """Roll the subscription into its next period and return the period that just ended.

    The ended period is returned as a (start, end) tuple of epoch seconds.
    """
    ended_period = (subscription["current_period_start"], subscription["current_period_end"])
    next_start = subscription["current_period_end"]
    subscription["current_period_start"] = next_start
    subscription["current_period_end"] = next_period_end(ctx, subscription, next_start)
    return ended_period


def is_billable(subscription):
    return subscription["status"] in BILLABLE_STATUSES
