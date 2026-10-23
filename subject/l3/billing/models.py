"""Core data shapes and constants for the billing engine."""

from collections.abc import Callable
from typing import Final, Literal, NewType, Protocol, TypedDict, Union

CustomerId = NewType("CustomerId", str)
PlanId = NewType("PlanId", str)
PriceId = NewType("PriceId", str)
SubscriptionId = NewType("SubscriptionId", str)
InvoiceId = NewType("InvoiceId", str)
PaymentId = NewType("PaymentId", str)
PaymentMethodId = NewType("PaymentMethodId", str)
CouponId = NewType("CouponId", str)
UsageRecordId = NewType("UsageRecordId", str)
LedgerEntryId = NewType("LedgerEntryId", str)
CreditNoteId = NewType("CreditNoteId", str)
EventId = NewType("EventId", str)
EndpointId = NewType("EndpointId", str)

Cents = NewType("Cents", int)
BasisPoints = NewType("BasisPoints", int)
EpochSeconds = NewType("EpochSeconds", int)
EpochMillis = NewType("EpochMillis", int)
CurrencyCode = NewType("CurrencyCode", str)

Interval = Literal["day", "week", "month", "year"]
PricingModel = Literal["flat", "per_unit", "tiered_graduated", "tiered_volume", "metered"]
UsageAggregation = Literal["sum", "max", "last"]
SubscriptionStatus = Literal["trialing", "active", "past_due", "paused", "canceled", "unpaid"]
InvoiceStatus = Literal["draft", "open", "paid", "void", "uncollectible"]
PaymentStatus = Literal["pending", "succeeded", "failed", "refunded", "partially_refunded"]
PaymentMethodKind = Literal["card", "bank_account"]
CouponDuration = Literal["once", "repeating", "forever"]
LineKind = Literal["subscription", "usage", "proration", "adjustment"]
LedgerReason = Literal["manual", "invoice_applied", "invoice_voided", "refund", "credit_note"]
DunningAction = Literal["cancel", "mark_unpaid", "pause"]
DeliveryStatus = Literal["pending", "delivered", "failed"]
EventType = Literal[
    "customer.created",
    "customer.updated",
    "subscription.created",
    "subscription.updated",
    "subscription.canceled",
    "invoice.created",
    "invoice.finalized",
    "invoice.paid",
    "invoice.voided",
    "invoice.payment_failed",
    "invoice.marked_uncollectible",
    "payment.refunded",
    "credit_note.created",
]

JSONValue = Union[str, int, float, bool, None, list["JSONValue"], dict[str, "JSONValue"]]
JSONObject = dict[str, JSONValue]


class Customer(TypedDict):
    id: CustomerId
    email: str
    name: str
    currency: CurrencyCode
    tax_region: str | None
    tax_exempt: bool
    default_payment_method: PaymentMethodId | None
    created_at: EpochSeconds
    metadata: dict[str, str]


class PaymentMethod(TypedDict):
    id: PaymentMethodId
    customer_id: CustomerId
    kind: PaymentMethodKind
    last4: str
    exp_month: int
    exp_year: int
    fingerprint: str


class Plan(TypedDict):
    id: PlanId
    name: str
    product_code: str
    trial_days: int
    created_at: EpochSeconds


class Tier(TypedDict):
    up_to: int | None
    unit_amount: Cents
    flat_amount: Cents


class Price(TypedDict):
    id: PriceId
    plan_id: PlanId
    currency: CurrencyCode
    model: PricingModel
    unit_amount: Cents
    tiers: list[Tier]
    interval: Interval
    interval_count: int
    usage_aggregation: UsageAggregation | None
    tax_inclusive: bool
    active: bool


class SubscriptionItem(TypedDict):
    price_id: PriceId
    quantity: int


class Subscription(TypedDict):
    id: SubscriptionId
    customer_id: CustomerId
    status: SubscriptionStatus
    items: list[SubscriptionItem]
    current_period_start: EpochSeconds
    current_period_end: EpochSeconds
    anchor_day: int
    trial_end: EpochSeconds | None
    cancel_at_period_end: bool
    canceled_at: EpochSeconds | None
    paused_at: EpochSeconds | None
    coupon_id: CouponId | None
    coupon_periods_used: int
    created_at: EpochSeconds


class InvoiceLine(TypedDict):
    description: str
    kind: LineKind
    price_id: PriceId | None
    quantity: int
    unit_amount: Cents
    amount: Cents
    discount_amount: Cents
    tax_rate: BasisPoints
    tax_amount: Cents
    tax_inclusive: bool
    period_start: EpochSeconds
    period_end: EpochSeconds


class Invoice(TypedDict):
    id: InvoiceId
    number: str | None
    customer_id: CustomerId
    subscription_id: SubscriptionId | None
    status: InvoiceStatus
    currency: CurrencyCode
    lines: list[InvoiceLine]
    subtotal: Cents
    discount_total: Cents
    tax_total: Cents
    total: Cents
    credit_applied: Cents
    amount_due: Cents
    amount_paid: Cents
    attempt_count: int
    next_attempt_at: EpochSeconds | None
    period_start: EpochSeconds
    period_end: EpochSeconds
    created_at: EpochSeconds
    finalized_at: EpochSeconds | None
    due_at: EpochSeconds | None
    paid_at: EpochSeconds | None
    voided_at: EpochSeconds | None


class Coupon(TypedDict):
    id: CouponId
    name: str
    percent_off: BasisPoints | None
    amount_off: Cents | None
    currency: CurrencyCode | None
    duration: CouponDuration
    duration_in_periods: int | None
    max_redemptions: int | None
    times_redeemed: int
    applies_to_plans: list[PlanId] | None
    valid_until: EpochSeconds | None


class UsageRecord(TypedDict):
    id: UsageRecordId
    subscription_id: SubscriptionId
    price_id: PriceId
    quantity: int
    timestamp: EpochMillis
    idempotency_key: str | None


class LedgerEntry(TypedDict):
    id: LedgerEntryId
    customer_id: CustomerId
    amount: Cents
    reason: LedgerReason
    invoice_id: InvoiceId | None
    created_at: EpochSeconds


class Payment(TypedDict):
    id: PaymentId
    invoice_id: InvoiceId
    customer_id: CustomerId
    method_id: PaymentMethodId
    amount: Cents
    currency: CurrencyCode
    status: PaymentStatus
    refunded_amount: Cents
    gateway_ref: str | None
    failure_code: str | None
    created_at: EpochSeconds


class CreditNote(TypedDict):
    id: CreditNoteId
    invoice_id: InvoiceId
    customer_id: CustomerId
    amount: Cents
    reason: str
    refunded: bool
    created_at: EpochSeconds


class TaxRate(TypedDict):
    region: str
    name: str
    rate: BasisPoints


class Event(TypedDict):
    id: EventId
    type: EventType
    created_at: EpochMillis
    payload: JSONObject


class WebhookEndpoint(TypedDict):
    id: EndpointId
    url: str
    secret: str
    event_types: list[EventType] | None
    enabled: bool
    created_at: EpochMillis


class WebhookDelivery(TypedDict):
    endpoint_id: EndpointId
    event_id: EventId
    status: DeliveryStatus
    attempts: int
    last_status_code: int | None
    next_attempt_at: EpochSeconds | None


class DunningPolicy(TypedDict):
    retry_offsets_days: list[int]
    final_action: DunningAction


class ChargeResult(TypedDict):
    ok: bool
    gateway_ref: str | None
    failure_code: str | None


class RefundResult(TypedDict):
    ok: bool
    gateway_ref: str | None


class Clock(Protocol):
    def now(self) -> EpochSeconds: ...


class PaymentGateway(Protocol):
    def charge(
        self, method: PaymentMethod, amount: Cents, currency: CurrencyCode, idempotency_key: str
    ) -> ChargeResult: ...

    def refund(self, gateway_ref: str, amount: Cents) -> RefundResult: ...


class Transport(Protocol):
    def post(self, url: str, body: bytes, headers: dict[str, str]) -> int: ...


EventListener = Callable[[Event], None]

DEFAULT_CURRENCY: Final[CurrencyCode] = CurrencyCode("usd")
CURRENCY_EXPONENTS: Final[dict[CurrencyCode, int]] = {
    CurrencyCode("usd"): 2,
    CurrencyCode("eur"): 2,
    CurrencyCode("gbp"): 2,
    CurrencyCode("jpy"): 0,
    CurrencyCode("kwd"): 3,
}
CURRENCY_SYMBOLS: Final[dict[CurrencyCode, str]] = {
    CurrencyCode("usd"): "$",
    CurrencyCode("eur"): "€",
    CurrencyCode("gbp"): "£",
    CurrencyCode("jpy"): "¥",
}
SECONDS_PER_DAY: Final[int] = 86_400
BASIS_POINTS_SCALE: Final[int] = 10_000
INVOICE_NET_DAYS: Final[int] = 7
