"""Exceptions raised by the billing engine."""


class BillingError(Exception):
    """Base class for all billing errors."""


class NotFoundError(BillingError):
    """A record looked up by key does not exist.

    `kind` is a human-readable record type (for example "customer") and `key` is the id
    that was looked up; both are kept on the exception for callers that want them.
    """

    def __init__(self, kind, key):
        super().__init__(f"{kind} not found: {key}")
        self.kind = kind
        self.key = key


class ValidationError(BillingError):
    """The caller passed an argument that is malformed or out of range."""


class InvalidStateError(BillingError):
    """The operation is not allowed in the record's current state."""


class CurrencyMismatchError(BillingError):
    """Amounts or prices in different currencies were combined."""
