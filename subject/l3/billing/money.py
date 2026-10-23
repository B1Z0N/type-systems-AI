"""Integer money arithmetic in minor currency units."""

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .errors import ValidationError
from .models import BASIS_POINTS_SCALE, CURRENCY_EXPONENTS, CURRENCY_SYMBOLS, BasisPoints, Cents, CurrencyCode


def exponent(currency: CurrencyCode) -> int:
    try:
        return CURRENCY_EXPONENTS[currency]
    except KeyError:
        raise ValidationError(f"unsupported currency: {currency}") from None


def is_supported(currency: CurrencyCode) -> bool:
    return currency in CURRENCY_EXPONENTS


def divide_round(numerator: int, denominator: int) -> int:
    """Integer division rounding half to even."""
    if denominator == 0:
        raise ZeroDivisionError("denominator must be non-zero")
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    quotient, remainder = divmod(numerator, denominator)
    doubled = remainder * 2
    if doubled > denominator or (doubled == denominator and quotient % 2 == 1):
        quotient += 1
    return quotient


def apply_basis_points(amount: Cents, rate: BasisPoints) -> Cents:
    return Cents(divide_round(amount * rate, BASIS_POINTS_SCALE))


def prorate(amount: Cents, used: int, total: int) -> Cents:
    if total <= 0:
        raise ValidationError("proration window must be positive")
    used = max(0, min(used, total))
    return Cents(divide_round(amount * used, total))


def total(values: Iterable[Cents]) -> Cents:
    return Cents(sum(values))


def allocate(amount: Cents, weights: Sequence[int]) -> list[Cents]:
    """Split amount across weights so that parts always sum to amount."""
    weight_sum = sum(weights)
    if weight_sum == 0:
        return [Cents(0) for _ in weights]
    shares = [amount * weight // weight_sum for weight in weights]
    remainders = [amount * weight % weight_sum for weight in weights]
    leftover = amount - sum(shares)
    order = sorted(range(len(weights)), key=lambda index: (-remainders[index], index))
    for index in order[:leftover]:
        shares[index] += 1
    return [Cents(share) for share in shares]


def parse_amount(text: str, currency: CurrencyCode) -> Cents:
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ValidationError(f"invalid amount: {text!r}") from None
    scaled = value.scaleb(exponent(currency)).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return Cents(int(scaled))


def to_decimal(amount: Cents, currency: CurrencyCode) -> Decimal:
    return Decimal(amount).scaleb(-exponent(currency))


def format_amount(amount: Cents, currency: CurrencyCode) -> str:
    digits = exponent(currency)
    sign = "-" if amount < 0 else ""
    major, minor = divmod(abs(amount), 10**digits)
    body = f"{major:,}" if digits == 0 else f"{major:,}.{minor:0{digits}d}"
    symbol = CURRENCY_SYMBOLS.get(currency)
    if symbol is None:
        return f"{sign}{body} {currency.upper()}"
    return f"{sign}{symbol}{body}"


def non_negative(amount: Cents) -> Cents:
    return Cents(max(0, amount))
