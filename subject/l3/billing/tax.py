"""Sales tax lookup and per-line tax calculation."""

from .context import Context
from .errors import ValidationError
from .models import BASIS_POINTS_SCALE, BasisPoints, Cents, Customer, TaxRate
from .money import divide_round


def set_tax_rate(ctx: Context, region: str, rate: BasisPoints, name: str) -> TaxRate:
    if not 0 <= rate < BASIS_POINTS_SCALE:
        raise ValidationError("tax rate must be between 0 and 100 percent")
    tax_rate: TaxRate = {"region": region.upper(), "name": name, "rate": rate}
    ctx.store.tax_rates[tax_rate["region"]] = tax_rate
    return tax_rate


def lookup(ctx: Context, region: str) -> TaxRate | None:
    """Find the most specific rate: 'US-CA' falls back to 'US'."""
    parts = region.upper().split("-")
    while parts:
        found = ctx.store.tax_rates.get("-".join(parts))
        if found is not None:
            return found
        parts.pop()
    return None


def rate_for_customer(ctx: Context, customer: Customer) -> BasisPoints:
    if customer["tax_exempt"]:
        return BasisPoints(0)
    region = customer["tax_region"]
    if region is None:
        return BasisPoints(0)
    found = lookup(ctx, region)
    return found["rate"] if found is not None else BasisPoints(0)


def split_tax(amount: Cents, rate: BasisPoints, inclusive: bool) -> tuple[Cents, Cents]:
    """Return (net, tax) for a taxable amount.

    Exclusive amounts are net already; inclusive amounts contain the tax.
    """
    if rate == 0:
        return amount, Cents(0)
    if inclusive:
        net = divide_round(amount * BASIS_POINTS_SCALE, BASIS_POINTS_SCALE + rate)
        return Cents(net), Cents(amount - net)
    return amount, Cents(divide_round(amount * rate, BASIS_POINTS_SCALE))
