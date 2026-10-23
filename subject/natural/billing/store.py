"""In-memory persistence for all billing records."""

from .errors import NotFoundError


def _require(table, key, kind):
    """Return `table[key]` or raise NotFoundError naming `kind`."""
    record = table.get(key)
    if record is None:
        raise NotFoundError(kind, key)
    return record


class Store:
    """Dict-of-dicts storage for every record type, keyed by id.

    `get_*` methods return None for unknown ids; `require_*` methods raise
    NotFoundError. The `*_for` helpers return lists in insertion order.
    """

    def __init__(self):
        self.customers = {}
        self.payment_methods = {}
        self.plans = {}
        self.prices = {}
        self.subscriptions = {}
        self.invoices = {}
        self.payments = {}
        self.coupons = {}
        self.usage_records = {}
        # Append-only list of balance changes, see ledger.py.
        self.ledger = []
        self.credit_notes = {}
        # Keyed by upper-case region code such as "US" or "US-CA".
        self.tax_rates = {}
        self.events = {}
        self.endpoints = {}
        self.deliveries = []
        # subscription id -> invoice lines to include on its next invoice.
        self.pending_items = {}
        # usage idempotency key -> usage record id.
        self.usage_keys = {}
        self._invoice_sequence = 0

    def get_customer(self, customer_id):
        return self.customers.get(customer_id)

    def require_customer(self, customer_id):
        return _require(self.customers, customer_id, "customer")

    def require_payment_method(self, method_id):
        return _require(self.payment_methods, method_id, "payment method")

    def require_plan(self, plan_id):
        return _require(self.plans, plan_id, "plan")

    def get_price(self, price_id):
        return self.prices.get(price_id)

    def require_price(self, price_id):
        return _require(self.prices, price_id, "price")

    def get_subscription(self, subscription_id):
        return self.subscriptions.get(subscription_id)

    def require_subscription(self, subscription_id):
        return _require(self.subscriptions, subscription_id, "subscription")

    def get_invoice(self, invoice_id):
        return self.invoices.get(invoice_id)

    def require_invoice(self, invoice_id):
        return _require(self.invoices, invoice_id, "invoice")

    def require_payment(self, payment_id):
        return _require(self.payments, payment_id, "payment")

    def get_coupon(self, coupon_id):
        return self.coupons.get(coupon_id)

    def require_coupon(self, coupon_id):
        return _require(self.coupons, coupon_id, "coupon")

    def require_endpoint(self, endpoint_id):
        return _require(self.endpoints, endpoint_id, "webhook endpoint")

    def require_event(self, event_id):
        return _require(self.events, event_id, "event")

    def payment_methods_for(self, customer_id):
        return [method for method in self.payment_methods.values() if method["customer_id"] == customer_id]

    def subscriptions_for(self, customer_id):
        return [subscription for subscription in self.subscriptions.values() if subscription["customer_id"] == customer_id]

    def invoices_for(self, customer_id):
        return [invoice for invoice in self.invoices.values() if invoice["customer_id"] == customer_id]

    def invoices_for_subscription(self, subscription_id):
        return [invoice for invoice in self.invoices.values() if invoice["subscription_id"] == subscription_id]

    def payments_for_invoice(self, invoice_id):
        return [payment for payment in self.payments.values() if payment["invoice_id"] == invoice_id]

    def usage_for(self, subscription_id, price_id):
        return [
            record
            for record in self.usage_records.values()
            if record["subscription_id"] == subscription_id and record["price_id"] == price_id
        ]

    def ledger_for(self, customer_id):
        return [entry for entry in self.ledger if entry["customer_id"] == customer_id]

    def credit_notes_for(self, invoice_id):
        return [note for note in self.credit_notes.values() if note["invoice_id"] == invoice_id]

    def take_pending_items(self, subscription_id):
        """Remove and return the subscription's pending invoice lines (empty list if none)."""
        return self.pending_items.pop(subscription_id, [])

    def next_invoice_number(self):
        """Allocate the next human-facing invoice number, e.g. "INV-00001"."""
        self._invoice_sequence += 1
        return f"INV-{self._invoice_sequence:05d}"
