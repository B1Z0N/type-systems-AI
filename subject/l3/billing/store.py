"""In-memory persistence for all billing records."""

from typing import TypeVar

from .errors import NotFoundError
from .models import (
    Coupon,
    CouponId,
    CreditNote,
    CreditNoteId,
    Customer,
    CustomerId,
    EndpointId,
    Event,
    EventId,
    Invoice,
    InvoiceId,
    InvoiceLine,
    LedgerEntry,
    Payment,
    PaymentId,
    PaymentMethod,
    PaymentMethodId,
    Plan,
    PlanId,
    Price,
    PriceId,
    Subscription,
    SubscriptionId,
    TaxRate,
    UsageRecord,
    UsageRecordId,
    WebhookDelivery,
    WebhookEndpoint,
)

K = TypeVar("K", bound=str)
V = TypeVar("V")


def _require(table: dict[K, V], key: K, kind: str) -> V:
    found = table.get(key)
    if found is None:
        raise NotFoundError(kind, key)
    return found


class Store:
    def __init__(self) -> None:
        self.customers: dict[CustomerId, Customer] = {}
        self.payment_methods: dict[PaymentMethodId, PaymentMethod] = {}
        self.plans: dict[PlanId, Plan] = {}
        self.prices: dict[PriceId, Price] = {}
        self.subscriptions: dict[SubscriptionId, Subscription] = {}
        self.invoices: dict[InvoiceId, Invoice] = {}
        self.payments: dict[PaymentId, Payment] = {}
        self.coupons: dict[CouponId, Coupon] = {}
        self.usage_records: dict[UsageRecordId, UsageRecord] = {}
        self.ledger: list[LedgerEntry] = []
        self.credit_notes: dict[CreditNoteId, CreditNote] = {}
        self.tax_rates: dict[str, TaxRate] = {}
        self.events: dict[EventId, Event] = {}
        self.endpoints: dict[EndpointId, WebhookEndpoint] = {}
        self.deliveries: list[WebhookDelivery] = []
        self.pending_items: dict[SubscriptionId, list[InvoiceLine]] = {}
        self.usage_keys: dict[str, UsageRecordId] = {}
        self._invoice_sequence = 0

    def get_customer(self, customer_id: CustomerId) -> Customer | None:
        return self.customers.get(customer_id)

    def require_customer(self, customer_id: CustomerId) -> Customer:
        return _require(self.customers, customer_id, "customer")

    def require_payment_method(self, method_id: PaymentMethodId) -> PaymentMethod:
        return _require(self.payment_methods, method_id, "payment method")

    def require_plan(self, plan_id: PlanId) -> Plan:
        return _require(self.plans, plan_id, "plan")

    def get_price(self, price_id: PriceId) -> Price | None:
        return self.prices.get(price_id)

    def require_price(self, price_id: PriceId) -> Price:
        return _require(self.prices, price_id, "price")

    def get_subscription(self, subscription_id: SubscriptionId) -> Subscription | None:
        return self.subscriptions.get(subscription_id)

    def require_subscription(self, subscription_id: SubscriptionId) -> Subscription:
        return _require(self.subscriptions, subscription_id, "subscription")

    def get_invoice(self, invoice_id: InvoiceId) -> Invoice | None:
        return self.invoices.get(invoice_id)

    def require_invoice(self, invoice_id: InvoiceId) -> Invoice:
        return _require(self.invoices, invoice_id, "invoice")

    def require_payment(self, payment_id: PaymentId) -> Payment:
        return _require(self.payments, payment_id, "payment")

    def get_coupon(self, coupon_id: CouponId) -> Coupon | None:
        return self.coupons.get(coupon_id)

    def require_coupon(self, coupon_id: CouponId) -> Coupon:
        return _require(self.coupons, coupon_id, "coupon")

    def require_endpoint(self, endpoint_id: EndpointId) -> WebhookEndpoint:
        return _require(self.endpoints, endpoint_id, "webhook endpoint")

    def require_event(self, event_id: EventId) -> Event:
        return _require(self.events, event_id, "event")

    def payment_methods_for(self, customer_id: CustomerId) -> list[PaymentMethod]:
        return [method for method in self.payment_methods.values() if method["customer_id"] == customer_id]

    def subscriptions_for(self, customer_id: CustomerId) -> list[Subscription]:
        return [sub for sub in self.subscriptions.values() if sub["customer_id"] == customer_id]

    def invoices_for(self, customer_id: CustomerId) -> list[Invoice]:
        return [invoice for invoice in self.invoices.values() if invoice["customer_id"] == customer_id]

    def invoices_for_subscription(self, subscription_id: SubscriptionId) -> list[Invoice]:
        return [inv for inv in self.invoices.values() if inv["subscription_id"] == subscription_id]

    def payments_for_invoice(self, invoice_id: InvoiceId) -> list[Payment]:
        return [payment for payment in self.payments.values() if payment["invoice_id"] == invoice_id]

    def usage_for(self, subscription_id: SubscriptionId, price_id: PriceId) -> list[UsageRecord]:
        return [
            record
            for record in self.usage_records.values()
            if record["subscription_id"] == subscription_id and record["price_id"] == price_id
        ]

    def ledger_for(self, customer_id: CustomerId) -> list[LedgerEntry]:
        return [entry for entry in self.ledger if entry["customer_id"] == customer_id]

    def credit_notes_for(self, invoice_id: InvoiceId) -> list[CreditNote]:
        return [note for note in self.credit_notes.values() if note["invoice_id"] == invoice_id]

    def take_pending_items(self, subscription_id: SubscriptionId) -> list[InvoiceLine]:
        return self.pending_items.pop(subscription_id, [])

    def next_invoice_number(self) -> str:
        self._invoice_sequence += 1
        return f"INV-{self._invoice_sequence:05d}"
