"""Sales tax lookup and per-line tax calculation.

Tax rates are in basis points (825 means 8.25%).
"""

from .errors import ValidationError
from .models import BASIS_POINTS_SCALE
from .money import divide_round


def set_tax_rate(ctx, region, rate, name):
    """Create or replace the tax rate for `region` (stored upper-case).

    `rate` is in basis points and must be in [0, 10_000); raises ValidationError otherwise.
    """
    if not 0 <= rate < BASIS_POINTS_SCALE:
        raise ValidationError("tax rate must be between 0 and 100 percent")
    tax_rate = {"region": region.upper(), "name": name, "rate": rate}
    ctx.store.tax_rates[tax_rate["region"]] = tax_rate
    return tax_rate


def lookup(ctx, region):
    """Find the most specific rate: 'US-CA' falls back to 'US'. Returns None if nothing matches."""
    region_parts = region.upper().split("-")
    while region_parts:
        tax_rate = ctx.store.tax_rates.get("-".join(region_parts))
        if tax_rate is not None:
            return tax_rate
        region_parts.pop()
    return None


def rate_for_customer(ctx, customer):
    """Tax rate in basis points for the customer; 0 if exempt or no rate applies."""
    if customer["tax_exempt"]:
        return 0
    region = customer["tax_region"]
    if region is None:
        return 0
    tax_rate = lookup(ctx, region)
    return tax_rate["rate"] if tax_rate is not None else 0


def split_tax(amount, rate, inclusive):
    """Return (net, tax) for a taxable amount.

    Exclusive amounts are net already; inclusive amounts contain the tax. `rate` is in
    basis points and rounding is half to even.
    """
    if rate == 0:
        return amount, 0
    if inclusive:
        net_amount = divide_round(amount * BASIS_POINTS_SCALE, BASIS_POINTS_SCALE + rate)
        return net_amount, amount - net_amount
    return amount, divide_round(amount * rate, BASIS_POINTS_SCALE)
