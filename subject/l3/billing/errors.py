"""Exceptions raised by the billing engine."""


class BillingError(Exception):
    """Base class for all billing errors."""


class NotFoundError(BillingError):
    def __init__(self, kind: str, key: str) -> None:
        super().__init__(f"{kind} not found: {key}")
        self.kind = kind
        self.key = key


class ValidationError(BillingError):
    pass


class InvalidStateError(BillingError):
    pass


class CurrencyMismatchError(BillingError):
    pass
