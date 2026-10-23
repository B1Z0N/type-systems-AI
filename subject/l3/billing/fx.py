"""Foreign exchange rates between supported currencies."""

from decimal import ROUND_HALF_EVEN, Decimal

from .errors import NotFoundError
from .models import Cents, CurrencyCode
from .money import exponent


class RateTable:
    def __init__(self) -> None:
        self._rates: dict[tuple[CurrencyCode, CurrencyCode], Decimal] = {}

    def set_rate(self, base: CurrencyCode, quote: CurrencyCode, rate: Decimal | str) -> None:
        value = Decimal(rate)
        if value <= 0:
            raise ValueError("rate must be positive")
        self._rates[(base, quote)] = value

    def rate(self, base: CurrencyCode, quote: CurrencyCode) -> Decimal:
        if base == quote:
            return Decimal(1)
        direct = self._rates.get((base, quote))
        if direct is not None:
            return direct
        inverse = self._rates.get((quote, base))
        if inverse is not None:
            return Decimal(1) / inverse
        raise NotFoundError("fx rate", f"{base}->{quote}")

    def convert(self, amount: Cents, source: CurrencyCode, target: CurrencyCode) -> Cents:
        """Convert minor units of source into minor units of target."""
        if source == target:
            return amount
        major = Decimal(amount).scaleb(-exponent(source))
        converted = (major * self.rate(source, target)).scaleb(exponent(target))
        return Cents(int(converted.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)))
