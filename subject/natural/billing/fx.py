"""Foreign exchange rates between supported currencies."""

from decimal import ROUND_HALF_EVEN, Decimal

from .errors import NotFoundError
from .money import exponent


class RateTable:
    """Exchange rates keyed by (base, quote) currency pair.

    A rate means "one major unit of base buys `rate` major units of quote". Missing
    pairs fall back to the inverse of the opposite pair.
    """

    def __init__(self):
        self._rate_by_pair = {}

    def set_rate(self, base, quote, rate):
        """Store a rate; `rate` may be anything Decimal accepts and must be positive."""
        rate_value = Decimal(rate)
        if rate_value <= 0:
            raise ValueError("rate must be positive")
        self._rate_by_pair[(base, quote)] = rate_value

    def rate(self, base, quote):
        """Decimal rate from `base` to `quote`; raises NotFoundError if no pair is known."""
        if base == quote:
            return Decimal(1)
        direct_rate = self._rate_by_pair.get((base, quote))
        if direct_rate is not None:
            return direct_rate
        inverse_rate = self._rate_by_pair.get((quote, base))
        if inverse_rate is not None:
            return Decimal(1) / inverse_rate
        raise NotFoundError("fx rate", f"{base}->{quote}")

    def convert(self, amount, source, target):
        """Convert minor units of `source` into minor units of `target`, rounding half to even."""
        if source == target:
            return amount
        source_major = Decimal(amount).scaleb(-exponent(source))
        target_minor = (source_major * self.rate(source, target)).scaleb(exponent(target))
        return int(target_minor.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
