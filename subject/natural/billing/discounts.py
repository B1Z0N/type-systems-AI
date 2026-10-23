"""Coupons and how they reduce invoice lines."""

from .errors import InvalidStateError, ValidationError
from .models import BASIS_POINTS_SCALE
from .money import allocate, apply_basis_points


def create_coupon(
    ctx,
    name,
    *,
    percent_off=None,
    amount_off=None,
    currency=None,
    duration="once",
    duration_in_periods=None,
    max_redemptions=None,
    applies_to_plans=None,
    valid_until=None,
):
    """Create and store a coupon.

    Exactly one of `percent_off` (basis points, so 2_500 is 25%) or `amount_off` (minor
    units of `currency`, which is then required) must be given. `duration` is "once",
    "forever" or "repeating"; repeating coupons last `duration_in_periods` invoices.
    `applies_to_plans` restricts the discount to lines of those plan ids (None means all
    plans) and `valid_until` is the epoch second after which it can't be redeemed.

    Raises ValidationError for inconsistent options.
    """
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
    coupon = {
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


def is_redeemable(coupon, at):
    """Whether the coupon can still be redeemed at epoch second `at`."""
    if coupon["valid_until"] is not None and at >= coupon["valid_until"]:
        return False
    if coupon["max_redemptions"] is not None and coupon["times_redeemed"] >= coupon["max_redemptions"]:
        return False
    return True


def redeem(ctx, subscription_id, coupon_id):
    """Attach a coupon to a subscription, replacing any previous one, and count the redemption.

    Raises InvalidStateError if the subscription is canceled or the coupon has expired
    or run out of redemptions.
    """
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


def applies_this_period(coupon, periods_used):
    """Whether the coupon still discounts an invoice after `periods_used` discounted invoices."""
    if coupon["duration"] == "forever":
        return True
    if coupon["duration"] == "once":
        return periods_used == 0
    period_limit = coupon["duration_in_periods"]
    return period_limit is not None and periods_used < period_limit


def _is_eligible(coupon, line, plan_id_by_price_id):
    """Coupons skip proration and non-positive lines, and honour the plan restriction."""
    if line["kind"] == "proration" or line["amount"] <= 0:
        return False
    allowed_plan_ids = coupon["applies_to_plans"]
    if allowed_plan_ids is None:
        return True
    price_id = line["price_id"]
    return price_id is not None and plan_id_by_price_id.get(price_id) in allowed_plan_ids


def line_discounts(coupon, lines, currency, plan_of):
    """Discount for each line in minor units, in the same order as `lines`.

    `plan_of` maps price id to plan id. Percentage coupons discount each eligible line
    independently. Fixed-amount coupons only apply when their currency matches
    `currency`; the amount is capped at the eligible total and spread across eligible
    lines in proportion to their amounts, so the parts add up exactly.
    """
    eligibility = [_is_eligible(coupon, line, plan_of) for line in lines]
    percent_off_bp = coupon["percent_off"]
    if percent_off_bp is not None:
        return [
            apply_basis_points(line["amount"], percent_off_bp) if is_eligible else 0
            for line, is_eligible in zip(lines, eligibility)
        ]
    amount_off = coupon["amount_off"]
    if amount_off is None or coupon["currency"] != currency:
        return [0 for _ in lines]
    weights = [line["amount"] if is_eligible else 0 for line, is_eligible in zip(lines, eligibility)]
    capped_discount = min(amount_off, sum(weights))
    return allocate(capped_discount, weights)


def remove_coupon(ctx, subscription_id):
    """Detach any coupon from the subscription."""
    subscription = ctx.store.require_subscription(subscription_id)
    subscription["coupon_id"] = None
    subscription["coupon_periods_used"] = 0
    return subscription
