"""Coupons and how they reduce invoice lines."""

from .context import Context
from .errors import InvalidStateError, ValidationError
from .models import (
    BASIS_POINTS_SCALE,
    BasisPoints,
    Cents,
    Coupon,
    CouponDuration,
    CouponId,
    CurrencyCode,
    EpochSeconds,
    InvoiceLine,
    PlanId,
    PriceId,
    Subscription,
    SubscriptionId,
)
from .money import allocate, apply_basis_points


def create_coupon(
    ctx: Context,
    name: str,
    *,
    percent_off: BasisPoints | None = None,
    amount_off: Cents | None = None,
    currency: CurrencyCode | None = None,
    duration: CouponDuration = "once",
    duration_in_periods: int | None = None,
    max_redemptions: int | None = None,
    applies_to_plans: list[PlanId] | None = None,
    valid_until: EpochSeconds | None = None,
) -> Coupon:
    if (percent_off is None) == (amount_off is None):
        raise ValidationError("exactly one of percent_off and amount_off is required")
    if percent_off is not None and not 0 < percent_off <= BASIS_POINTS_SCALE:
        raise ValidationError("percent_off must be within (0, 100] percent")
    if amount_off is not None:
        if amount_off <= 0:
            raise ValidationError("amount_off must be positive")
        if currency is None:
            raise ValidationError("amount_off coupons need a currency")
    if duration == "repeating":
        if duration_in_periods is None or duration_in_periods < 1:
            raise ValidationError("repeating coupons need duration_in_periods >= 1")
    elif duration_in_periods is not None:
        raise ValidationError("duration_in_periods only applies to repeating coupons")
    coupon: Coupon = {
        "id": ctx.ids.coupon(),
        "name": name,
        "percent_off": percent_off,
        "amount_off": amount_off,
        "currency": currency,
        "duration": duration,
        "duration_in_periods": duration_in_periods,
        "max_redemptions": max_redemptions,
        "times_redeemed": 0,
        "applies_to_plans": applies_to_plans,
        "valid_until": valid_until,
    }
    ctx.store.coupons[coupon["id"]] = coupon
    return coupon


def is_redeemable(coupon: Coupon, at: EpochSeconds) -> bool:
    if coupon["valid_until"] is not None and at >= coupon["valid_until"]:
        return False
    if coupon["max_redemptions"] is not None and coupon["times_redeemed"] >= coupon["max_redemptions"]:
        return False
    return True


def redeem(ctx: Context, subscription_id: SubscriptionId, coupon_id: CouponId) -> Subscription:
    subscription = ctx.store.require_subscription(subscription_id)
    coupon = ctx.store.require_coupon(coupon_id)
    if subscription["status"] == "canceled":
        raise InvalidStateError("cannot apply a coupon to a canceled subscription")
    if not is_redeemable(coupon, ctx.clock.now()):
        raise InvalidStateError(f"coupon {coupon_id} can no longer be redeemed")
    coupon["times_redeemed"] += 1
    subscription["coupon_id"] = coupon_id
    subscription["coupon_periods_used"] = 0
    return subscription


def applies_this_period(coupon: Coupon, periods_used: int) -> bool:
    if coupon["duration"] == "forever":
        return True
    if coupon["duration"] == "once":
        return periods_used == 0
    limit = coupon["duration_in_periods"]
    return limit is not None and periods_used < limit


def _eligible(coupon: Coupon, line: InvoiceLine, plan_of: dict[PriceId, PlanId]) -> bool:
    if line["kind"] == "proration" or line["amount"] <= 0:
        return False
    plans = coupon["applies_to_plans"]
    if plans is None:
        return True
    price_id = line["price_id"]
    return price_id is not None and plan_of.get(price_id) in plans


def line_discounts(
    coupon: Coupon, lines: list[InvoiceLine], currency: CurrencyCode, plan_of: dict[PriceId, PlanId]
) -> list[Cents]:
    """Discount for each line, in the same order as lines."""
    eligible = [_eligible(coupon, line, plan_of) for line in lines]
    percent = coupon["percent_off"]
    if percent is not None:
        return [apply_basis_points(line["amount"], percent) if ok else Cents(0) for line, ok in zip(lines, eligible)]
    amount_off = coupon["amount_off"]
    if amount_off is None or coupon["currency"] != currency:
        return [Cents(0) for _ in lines]
    weights = [line["amount"] if ok else 0 for line, ok in zip(lines, eligible)]
    cap = min(amount_off, sum(weights))
    return allocate(Cents(cap), weights)


def remove_coupon(ctx: Context, subscription_id: SubscriptionId) -> Subscription:
    subscription = ctx.store.require_subscription(subscription_id)
    subscription["coupon_id"] = None
    subscription["coupon_periods_used"] = 0
    return subscription
