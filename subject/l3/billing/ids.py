"""Deterministic, prefixed identifier generation."""

from typing import Final

from .models import (
    CouponId,
    CreditNoteId,
    CustomerId,
    EndpointId,
    EventId,
    InvoiceId,
    LedgerEntryId,
    PaymentId,
    PaymentMethodId,
    PlanId,
    PriceId,
    SubscriptionId,
    UsageRecordId,
)

PREFIXES: Final[tuple[str, ...]] = (
    "cus", "pm", "plan", "price", "sub", "in", "py", "cpn", "ur", "le", "cn", "evt", "we",
)


class IdGenerator:
    def __init__(self) -> None:
        self._counters: dict[str, int] = {prefix: 0 for prefix in PREFIXES}

    def next(self, prefix: str) -> str:
        if prefix not in self._counters:
            raise ValueError(f"unknown id prefix: {prefix}")
        self._counters[prefix] += 1
        return f"{prefix}_{self._counters[prefix]:06d}"

    def customer(self) -> CustomerId:
        return CustomerId(self.next("cus"))

    def payment_method(self) -> PaymentMethodId:
        return PaymentMethodId(self.next("pm"))

    def plan(self) -> PlanId:
        return PlanId(self.next("plan"))

    def price(self) -> PriceId:
        return PriceId(self.next("price"))

    def subscription(self) -> SubscriptionId:
        return SubscriptionId(self.next("sub"))

    def invoice(self) -> InvoiceId:
        return InvoiceId(self.next("in"))

    def payment(self) -> PaymentId:
        return PaymentId(self.next("py"))

    def coupon(self) -> CouponId:
        return CouponId(self.next("cpn"))

    def usage_record(self) -> UsageRecordId:
        return UsageRecordId(self.next("ur"))

    def ledger_entry(self) -> LedgerEntryId:
        return LedgerEntryId(self.next("le"))

    def credit_note(self) -> CreditNoteId:
        return CreditNoteId(self.next("cn"))

    def event(self) -> EventId:
        return EventId(self.next("evt"))

    def endpoint(self) -> EndpointId:
        return EndpointId(self.next("we"))
