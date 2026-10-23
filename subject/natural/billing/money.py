"""Integer money arithmetic in minor currency units.

Every amount in the engine is an int count of the currency's minor unit (cents for usd,
yen for jpy, fils for kwd). Floats never appear in money paths.
"""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .errors import ValidationError
from .models import BASIS_POINTS_SCALE, CURRENCY_EXPONENTS, CURRENCY_SYMBOLS


def exponent(currency):
    """Number of decimal places in `currency`'s minor unit (2 for usd, 0 for jpy).

    Raises ValidationError for an unsupported currency code.
    """
    try:
        return CURRENCY_EXPONENTS[currency]
    except KeyError:
        raise ValidationError(f"unsupported currency: {currency}") from None


def is_supported(currency):
    return currency in CURRENCY_EXPONENTS


def divide_round(numerator, denominator):
    """Integer division rounding half to even (banker's rounding).

    Works for negative operands; raises ZeroDivisionError when `denominator` is 0.
    """
    if denominator == 0:
        raise ZeroDivisionError("denominator must be non-zero")
    # Normalise so the divisor is positive; divmod then always yields a non-negative remainder.
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    quotient, remainder = divmod(numerator, denominator)
    doubled_remainder = remainder * 2
    if doubled_remainder > denominator or (doubled_remainder == denominator and quotient % 2 == 1):
        quotient += 1
    return quotient


def apply_basis_points(amount, rate):
    """Return `rate` basis points of `amount`, rounded half to even (e.g. 2_500 bp is 25%)."""
    return divide_round(amount * rate, BASIS_POINTS_SCALE)


def prorate(amount, used, total):
    """Share of `amount` covering `used` out of `total` units (usually seconds).

    `used` is clamped into [0, total]. Raises ValidationError if `total` is not positive.
    """
    if total <= 0:
        raise ValidationError("proration window must be positive")
    clamped_used = max(0, min(used, total))
    return divide_round(amount * clamped_used, total)


def total(values):
    """Sum of an iterable of amounts (0 when empty)."""
    return sum(values)


def allocate(amount, weights):
    """Split `amount` proportionally to `weights` so the parts always sum to `amount`.

    Each part starts at its floored share; the leftover units go one at a time to the
    parts with the largest remainders, ties broken by position. Returns a new list in
    the same order as `weights`, or all zeros if the weights sum to zero.
    """
    weight_sum = sum(weights)
    if weight_sum == 0:
        return [0 for _ in weights]
    shares = [amount * weight // weight_sum for weight in weights]
    remainders = [amount * weight % weight_sum for weight in weights]
    leftover_units = amount - sum(shares)
    by_largest_remainder = sorted(range(len(weights)), key=lambda index: (-remainders[index], index))
    for index in by_largest_remainder[:leftover_units]:
        shares[index] += 1
    return list(shares)


def parse_amount(text, currency):
    """Parse a major-unit string such as "12.34" into minor units of `currency`.

    Extra precision is rounded half to even. Raises ValidationError if the text is not a
    number or the currency is unsupported.
    """
    try:
        major_units = Decimal(text.strip())
    except InvalidOperation:
        raise ValidationError(f"invalid amount: {text!r}") from None
    minor_units = major_units.scaleb(exponent(currency)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return int(minor_units)


def to_decimal(amount, currency):
    """Convert minor units into an exact Decimal in major units (1234 usd -> 12.34)."""
    return Decimal(amount).scaleb(-exponent(currency))


def format_amount(amount, currency):
    """Human-readable amount, e.g. "$1,234.50", "-¥500" or "1.250 KWD".

    Currencies without a known symbol are suffixed with their upper-case code.
    """
    decimal_places = exponent(currency)
    sign = "-" if amount < 0 else ""
    major, minor = divmod(abs(amount), 10**decimal_places)
    body = f"{major:,}" if decimal_places == 0 else f"{major:,}.{minor:0{decimal_places}d}"
    symbol = CURRENCY_SYMBOLS.get(currency)
    if symbol is None:
        return f"{sign}{body} {currency.upper()}"
    return f"{sign}{symbol}{body}"


def non_negative(amount):
    """Clamp an amount at zero."""
    return max(0, amount)
