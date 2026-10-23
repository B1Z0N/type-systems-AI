"""Turning a price and a quantity into an amount."""

from .errors import ValidationError
from .money import format_amount


def tier_for_quantity(tiers, quantity):
    """The first tier whose `up_to` bound covers `quantity`; raises ValidationError if none does."""
    for tier in tiers:
        upper_bound = tier["up_to"]
        if upper_bound is None or quantity <= upper_bound:
            return tier
    raise ValidationError("no tier covers the quantity")


def _graduated_amount(tiers, quantity):
    """Each tier prices only the units that fall inside it, plus its flat fee once touched."""
    amount = 0
    units_priced = 0
    for tier in tiers:
        if quantity <= units_priced:
            break
        upper_bound = tier["up_to"]
        tier_ceiling = quantity if upper_bound is None else min(quantity, upper_bound)
        units_in_tier = tier_ceiling - units_priced
        amount += units_in_tier * tier["unit_amount"] + tier["flat_amount"]
        units_priced = tier_ceiling
    return amount


def _volume_amount(tiers, quantity):
    """All units are priced at the single tier the total quantity falls into."""
    if quantity == 0:
        return 0
    tier = tier_for_quantity(tiers, quantity)
    return quantity * tier["unit_amount"] + tier["flat_amount"]


def amount_for_quantity(price, quantity):
    """Amount in minor units that `price` charges for `quantity` units in one period.

    Flat prices ignore the quantity. Raises ValidationError for a negative quantity.
    """
    if quantity < 0:
        raise ValidationError("quantity cannot be negative")
    model = price["model"]
    if model == "flat":
        return price["unit_amount"]
    if model in ("per_unit", "metered"):
        return price["unit_amount"] * quantity
    if model == "tiered_graduated":
        return _graduated_amount(price["tiers"], quantity)
    return _volume_amount(price["tiers"], quantity)


def describe(price):
    """Short human-readable summary such as "$10.00 per unit / month"."""
    if price["interval_count"] == 1:
        billing_every = price["interval"]
    else:
        billing_every = f"{price['interval_count']} {price['interval']}s"
    model = price["model"]
    if model == "flat":
        return f"{format_amount(price['unit_amount'], price['currency'])} / {billing_every}"
    if model in ("per_unit", "metered"):
        unit_label = "unit" if model == "per_unit" else "unit used"
        return f"{format_amount(price['unit_amount'], price['currency'])} per {unit_label} / {billing_every}"
    return f"tiered ({model.removeprefix('tiered_')}) / {billing_every}"
