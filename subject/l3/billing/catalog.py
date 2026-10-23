"""Plans and prices that customers can subscribe to."""

from .context import Context
from .errors import ValidationError
from .models import (
    Cents,
    CurrencyCode,
    Interval,
    Plan,
    PlanId,
    Price,
    PriceId,
    PricingModel,
    Tier,
    UsageAggregation,
)
from .money import is_supported


def create_plan(ctx: Context, name: str, product_code: str, trial_days: int = 0) -> Plan:
    if trial_days < 0:
        raise ValidationError("trial_days cannot be negative")
    plan: Plan = {
        "id": ctx.ids.plan(),
        "name": name,
        "product_code": product_code,
        "trial_days": trial_days,
        "created_at": ctx.clock.now(),
    }
    ctx.store.plans[plan["id"]] = plan
    return plan


def _validate_tiers(tiers: list[Tier]) -> None:
    if not tiers:
        raise ValidationError("tiered prices need at least one tier")
    previous = 0
    for index, tier in enumerate(tiers):
        bound = tier["up_to"]
        last = index == len(tiers) - 1
        if bound is None and not last:
            raise ValidationError("only the last tier may be unbounded")
        if bound is not None and bound <= previous:
            raise ValidationError("tier bounds must be strictly increasing")
        if last and bound is not None:
            raise ValidationError("the last tier must be unbounded")
        previous = bound if bound is not None else previous


def create_price(
    ctx: Context,
    plan_id: PlanId,
    currency: CurrencyCode,
    model: PricingModel,
    *,
    unit_amount: Cents = Cents(0),
    tiers: list[Tier] | None = None,
    interval: Interval = "month",
    interval_count: int = 1,
    usage_aggregation: UsageAggregation | None = None,
    tax_inclusive: bool = False,
) -> Price:
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
    price: Price = {
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


def archive_price(ctx: Context, price_id: PriceId) -> Price:
    price = ctx.store.require_price(price_id)
    price["active"] = False
    return price


def prices_for_plan(ctx: Context, plan_id: PlanId) -> list[Price]:
    return [price for price in ctx.store.prices.values() if price["plan_id"] == plan_id]


def find_price(ctx: Context, plan_id: PlanId, currency: CurrencyCode, interval: Interval) -> Price | None:
    for price in prices_for_plan(ctx, plan_id):
        if price["active"] and price["currency"] == currency and price["interval"] == interval:
            return price
    return None


def is_metered(price: Price) -> bool:
    return price["model"] == "metered"
