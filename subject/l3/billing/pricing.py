"""Turning a price and a quantity into an amount."""

from .errors import ValidationError
from .models import Cents, Price, Tier
from .money import format_amount


def tier_for_quantity(tiers: list[Tier], quantity: int) -> Tier:
    for tier in tiers:
        bound = tier["up_to"]
        if bound is None or quantity <= bound:
            return tier
    raise ValidationError("no tier covers the quantity")


def _graduated(tiers: list[Tier], quantity: int) -> Cents:
    amount = 0
    lower = 0
    for tier in tiers:
        if quantity <= lower:
            break
        bound = tier["up_to"]
        upper = quantity if bound is None else min(quantity, bound)
        units = upper - lower
        amount += units * tier["unit_amount"] + tier["flat_amount"]
        lower = upper
    return Cents(amount)


def _volume(tiers: list[Tier], quantity: int) -> Cents:
    if quantity == 0:
        return Cents(0)
    tier = tier_for_quantity(tiers, quantity)
    return Cents(quantity * tier["unit_amount"] + tier["flat_amount"])


def amount_for_quantity(price: Price, quantity: int) -> Cents:
    if quantity < 0:
        raise ValidationError("quantity cannot be negative")
    model = price["model"]
    if model == "flat":
        return price["unit_amount"]
    if model in ("per_unit", "metered"):
        return Cents(price["unit_amount"] * quantity)
    if model == "tiered_graduated":
        return _graduated(price["tiers"], quantity)
    return _volume(price["tiers"], quantity)


def describe(price: Price) -> str:
    every = price["interval"] if price["interval_count"] == 1 else f"{price['interval_count']} {price['interval']}s"
    model = price["model"]
    if model == "flat":
        return f"{format_amount(price['unit_amount'], price['currency'])} / {every}"
    if model in ("per_unit", "metered"):
        unit = "unit" if model == "per_unit" else "unit used"
        return f"{format_amount(price['unit_amount'], price['currency'])} per {unit} / {every}"
    return f"tiered ({model.removeprefix('tiered_')}) / {every}"
