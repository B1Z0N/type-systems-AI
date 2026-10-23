"""Plans and prices that customers can subscribe to."""

from .errors import ValidationError
from .money import is_supported


def create_plan(ctx, name, product_code, trial_days=0):
    """Create and store a plan; `trial_days` is the default free trial for its prices.

    Raises ValidationError if `trial_days` is negative.
    """
    if trial_days < 0:
        raise ValidationError("trial_days cannot be negative")
    plan = {
        "id": ctx.ids.plan(),
        "name": name,
        "product_code": product_code,
        "trial_days": trial_days,
        "created_at": ctx.clock.now(),
    }
    ctx.store.plans[plan["id"]] = plan
    return plan


def _validate_tiers(tiers):
    """Tiers must have strictly increasing `up_to` bounds and end with one unbounded tier."""
    if not tiers:
        raise ValidationError("tiered prices need at least one tier")
    previous_bound = 0
    for index, tier in enumerate(tiers):
        upper_bound = tier["up_to"]
        is_last = index == len(tiers) - 1
        if upper_bound is None and not is_last:
            raise ValidationError("only the last tier may be unbounded")
        if upper_bound is not None and upper_bound <= previous_bound:
            raise ValidationError("tier bounds must be strictly increasing")
        if is_last and upper_bound is not None:
            raise ValidationError("the last tier must be unbounded")
        if upper_bound is not None:
            previous_bound = upper_bound


def create_price(
    ctx,
    plan_id,
    currency,
    model,
    *,
    unit_amount=0,
    tiers=None,
    interval="month",
    interval_count=1,
    usage_aggregation=None,
    tax_inclusive=False,
):
    """Create and store a price for an existing plan.

    `model` is one of "flat", "per_unit", "metered", "tiered_graduated" or
    "tiered_volume". `unit_amount` is in minor units of `currency`. Tiered models take
    `tiers`, a list of {"up_to": int or None, "unit_amount": int, "flat_amount": int};
    metered prices need a `usage_aggregation` ("sum", "max" or "last"). The price bills
    every `interval_count` `interval`s ("day", "week", "month" or "year").

    Raises NotFoundError for an unknown plan and ValidationError for inconsistent options.
    """
    ctx.store.require_plan(plan_id)
    if not is_supported(currency):
        raise ValidationError(f"unsupported currency: {currency}")
    if interval_count < 1:
        raise ValidationError("interval_count must be at least 1")
    if unit_amount < 0:
        raise ValidationError("unit_amount cannot be negative")
    resolved_tiers = list(tiers or [])
    if model in ("tiered_graduated", "tiered_volume"):
        _validate_tiers(resolved_tiers)
    elif resolved_tiers:
        raise ValidationError(f"{model} prices cannot have tiers")
    if model == "metered" and usage_aggregation is None:
        raise ValidationError("metered prices need a usage aggregation")
    if model != "metered" and usage_aggregation is not None:
        raise ValidationError("only metered prices aggregate usage")
    price = {
        "id": ctx.ids.price(),
        "plan_id": plan_id,
        "currency": currency,
        "model": model,
        "unit_amount": unit_amount,
        "tiers": resolved_tiers,
        "interval": interval,
        "interval_count": interval_count,
        "usage_aggregation": usage_aggregation,
        "tax_inclusive": tax_inclusive,
        "active": True,
    }
    ctx.store.prices[price["id"]] = price
    return price


def archive_price(ctx, price_id):
    """Stop offering a price to new subscriptions; existing subscriptions keep it."""
    price = ctx.store.require_price(price_id)
    price["active"] = False
    return price


def prices_for_plan(ctx, plan_id):
    """All prices of a plan, archived ones included, in creation order."""
    return [price for price in ctx.store.prices.values() if price["plan_id"] == plan_id]


def find_price(ctx, plan_id, currency, interval):
    """First active price of the plan matching currency and interval, or None."""
    for price in prices_for_plan(ctx, plan_id):
        if price["active"] and price["currency"] == currency and price["interval"] == interval:
            return price
    return None


def is_metered(price):
    return price["model"] == "metered"
