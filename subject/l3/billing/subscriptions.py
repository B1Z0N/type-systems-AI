"""Subscription lifecycle and state transitions."""

from typing import Final

from .clock import add_interval, civil
from .context import Context
from .discounts import is_redeemable, redeem
from .errors import CurrencyMismatchError, InvalidStateError, ValidationError
from .events import emit
from .invoices import add_pending_item
from .models import (
    SECONDS_PER_DAY,
    CouponId,
    CustomerId,
    EpochSeconds,
    EventType,
    Interval,
    Price,
    PriceId,
    Subscription,
    SubscriptionId,
    SubscriptionItem,
    SubscriptionStatus,
)
from .proration import proration_lines
from .serialization import subscription_json

ALLOWED_TRANSITIONS: Final[dict[SubscriptionStatus, frozenset[SubscriptionStatus]]] = {
    "trialing": frozenset({"active", "past_due", "canceled", "paused"}),
    "active": frozenset({"past_due", "paused", "canceled"}),
    "past_due": frozenset({"active", "unpaid", "canceled", "paused"}),
    "unpaid": frozenset({"active", "canceled"}),
    "paused": frozenset({"active", "canceled"}),
    "canceled": frozenset(),
}

BILLABLE_STATUSES: Final[frozenset[SubscriptionStatus]] = frozenset({"trialing", "active", "past_due"})


def transition(ctx: Context, subscription: Subscription, status: SubscriptionStatus) -> None:
    current = subscription["status"]
    if status == current:
        return
    if status not in ALLOWED_TRANSITIONS[current]:
        raise InvalidStateError(f"subscription cannot go from {current} to {status}")
    subscription["status"] = status
    now = ctx.clock.now()
    event: EventType = "subscription.updated"
    if status == "canceled":
        subscription["canceled_at"] = now
        event = "subscription.canceled"
    if status == "paused":
        subscription["paused_at"] = now
    elif current == "paused":
        subscription["paused_at"] = None
    emit(ctx, event, {"subscription": subscription_json(subscription), "previous_status": current})


def _resolve_items(ctx: Context, items: list[tuple[PriceId, int]]) -> tuple[list[SubscriptionItem], list[Price]]:
    if not items:
        raise ValidationError("a subscription needs at least one item")
    seen: set[PriceId] = set()
    resolved: list[SubscriptionItem] = []
    prices: list[Price] = []
    for price_id, quantity in items:
        if price_id in seen:
            raise ValidationError(f"duplicate price in subscription: {price_id}")
        seen.add(price_id)
        price = ctx.store.require_price(price_id)
        if not price["active"]:
            raise ValidationError(f"price {price_id} is archived")
        if quantity < 0 or (quantity == 0 and price["model"] != "metered"):
            raise ValidationError("licensed items need a positive quantity")
        resolved.append({"price_id": price_id, "quantity": quantity})
        prices.append(price)
    first = prices[0]
    for price in prices[1:]:
        if (price["interval"], price["interval_count"]) != (first["interval"], first["interval_count"]):
            raise ValidationError("all prices in a subscription must share a billing interval")
        if price["currency"] != first["currency"]:
            raise CurrencyMismatchError("all prices in a subscription must share a currency")
    return resolved, prices


def billing_interval(ctx: Context, subscription: Subscription) -> tuple[Interval, int]:
    price = ctx.store.require_price(subscription["items"][0]["price_id"])
    return price["interval"], price["interval_count"]


def next_period_end(ctx: Context, subscription: Subscription, start: EpochSeconds) -> EpochSeconds:
    interval, count = billing_interval(ctx, subscription)
    return add_interval(start, interval, count, subscription["anchor_day"])


def create(
    ctx: Context,
    customer_id: CustomerId,
    items: list[tuple[PriceId, int]],
    *,
    trial_days: int | None = None,
    coupon_id: CouponId | None = None,
) -> Subscription:
    customer = ctx.store.require_customer(customer_id)
    resolved, prices = _resolve_items(ctx, items)
    if prices[0]["currency"] != customer["currency"]:
        raise CurrencyMismatchError("subscription currency must match the customer currency")
    if coupon_id is not None and not is_redeemable(ctx.store.require_coupon(coupon_id), ctx.clock.now()):
        raise InvalidStateError(f"coupon {coupon_id} can no longer be redeemed")
    if trial_days is None:
        trial_days = max(ctx.store.require_plan(price["plan_id"])["trial_days"] for price in prices)
    now = ctx.clock.now()
    subscription: Subscription = {
        "id": ctx.ids.subscription(),
        "customer_id": customer_id,
        "status": "trialing" if trial_days > 0 else "active",
        "items": resolved,
        "current_period_start": now,
        "current_period_end": now,
        "anchor_day": civil(now).day,
        "trial_end": EpochSeconds(now + trial_days * SECONDS_PER_DAY) if trial_days > 0 else None,
        "cancel_at_period_end": False,
        "canceled_at": None,
        "paused_at": None,
        "coupon_id": None,
        "coupon_periods_used": 0,
        "created_at": now,
    }
    trial_end = subscription["trial_end"]
    subscription["current_period_end"] = (
        trial_end if trial_end is not None else next_period_end(ctx, subscription, now)
    )
    ctx.store.subscriptions[subscription["id"]] = subscription
    if coupon_id is not None:
        redeem(ctx, subscription["id"], coupon_id)
    emit(ctx, "subscription.created", {"subscription": subscription_json(subscription)})
    return subscription


def change_items(
    ctx: Context,
    subscription_id: SubscriptionId,
    items: list[tuple[PriceId, int]],
    *,
    prorate: bool = True,
) -> Subscription:
    subscription = ctx.store.require_subscription(subscription_id)
    if subscription["status"] not in ("active", "trialing", "past_due"):
        raise InvalidStateError(f"cannot change a {subscription['status']} subscription")
    resolved, prices = _resolve_items(ctx, items)
    customer = ctx.store.require_customer(subscription["customer_id"])
    if prices[0]["currency"] != customer["currency"]:
        raise CurrencyMismatchError("new prices must use the customer currency")
    old_interval = billing_interval(ctx, subscription)
    if (prices[0]["interval"], prices[0]["interval_count"]) != old_interval:
        raise ValidationError("changing the billing interval is not supported")
    old_items = subscription["items"]
    if prorate and subscription["status"] != "trialing":
        for line in proration_lines(ctx, subscription, old_items, resolved, ctx.clock.now()):
            add_pending_item(ctx, subscription, line)
    subscription["items"] = resolved
    emit(ctx, "subscription.updated", {"subscription": subscription_json(subscription), "previous_status": subscription["status"]})
    return subscription


def cancel(ctx: Context, subscription_id: SubscriptionId, *, at_period_end: bool = True) -> Subscription:
    subscription = ctx.store.require_subscription(subscription_id)
    if subscription["status"] == "canceled":
        raise InvalidStateError("subscription is already canceled")
    if at_period_end and subscription["status"] in ("active", "trialing", "past_due"):
        subscription["cancel_at_period_end"] = True
        emit(ctx, "subscription.updated", {"subscription": subscription_json(subscription), "previous_status": subscription["status"]})
        return subscription
    transition(ctx, subscription, "canceled")
    ctx.store.pending_items.pop(subscription_id, None)
    return subscription


def reactivate(ctx: Context, subscription_id: SubscriptionId) -> Subscription:
    subscription = ctx.store.require_subscription(subscription_id)
    if not subscription["cancel_at_period_end"]:
        raise InvalidStateError("subscription is not scheduled for cancellation")
    subscription["cancel_at_period_end"] = False
    emit(ctx, "subscription.updated", {"subscription": subscription_json(subscription), "previous_status": subscription["status"]})
    return subscription


def pause(ctx: Context, subscription_id: SubscriptionId) -> Subscription:
    subscription = ctx.store.require_subscription(subscription_id)
    transition(ctx, subscription, "paused")
    return subscription


def resume(ctx: Context, subscription_id: SubscriptionId) -> Subscription:
    """Resume a paused subscription with a fresh period starting now."""
    subscription = ctx.store.require_subscription(subscription_id)
    if subscription["status"] != "paused":
        raise InvalidStateError("only paused subscriptions can be resumed")
    now = ctx.clock.now()
    transition(ctx, subscription, "active")
    subscription["current_period_start"] = now
    subscription["anchor_day"] = civil(now).day
    subscription["current_period_end"] = next_period_end(ctx, subscription, now)
    return subscription


def advance_period(ctx: Context, subscription: Subscription) -> tuple[EpochSeconds, EpochSeconds]:
    """Roll the subscription into its next period and return the period that just ended."""
    ended = (subscription["current_period_start"], subscription["current_period_end"])
    start = subscription["current_period_end"]
    subscription["current_period_start"] = start
    subscription["current_period_end"] = next_period_end(ctx, subscription, start)
    return ended


def is_billable(subscription: Subscription) -> bool:
    return subscription["status"] in BILLABLE_STATUSES
