"""High-level facade that wires the billing modules together."""

from . import catalog, customers, discounts, invoices, payments, refunds, subscriptions, usage, webhooks
from .context import Context
from .dunning import DEFAULT_POLICY, due_for_retry
from .fx import RateTable
from .models import (
    Clock,
    CouponId,
    CreditNote,
    CustomerId,
    DunningPolicy,
    EpochMillis,
    EpochSeconds,
    Invoice,
    InvoiceId,
    Payment,
    PaymentGateway,
    PriceId,
    Subscription,
    SubscriptionId,
    Transport,
    UsageRecord,
)


class BillingEngine:
    def __init__(
        self,
        clock: Clock,
        gateway: PaymentGateway,
        transport: Transport,
        dunning_policy: DunningPolicy | None = None,
        fx: RateTable | None = None,
    ) -> None:
        self.clock = clock
        self.ctx = Context(clock, gateway, transport, dunning_policy or DEFAULT_POLICY, fx)

    def subscribe(
        self,
        customer_id: CustomerId,
        items: list[tuple[PriceId, int]],
        *,
        trial_days: int | None = None,
        coupon_id: CouponId | None = None,
    ) -> Subscription:
        """Create a subscription and, unless it starts with a trial, invoice the first period."""
        subscription = subscriptions.create(
            self.ctx, customer_id, items, trial_days=trial_days, coupon_id=coupon_id
        )
        if subscription["status"] == "active":
            self._invoice_period(subscription, None)
        return subscription

    def _invoice_period(
        self, subscription: Subscription, usage_period: tuple[EpochSeconds, EpochSeconds] | None
    ) -> Invoice:
        draft = invoices.build_draft(
            self.ctx,
            subscription,
            subscription["current_period_start"],
            subscription["current_period_end"],
            usage_period,
        )
        invoice = invoices.finalize(self.ctx, draft["id"])
        if invoice["status"] == "open":
            payments.collect(self.ctx, invoice["id"])
        return invoice

    def _renew(self, subscription: Subscription) -> Invoice | None:
        if subscription["cancel_at_period_end"]:
            ended = subscriptions.advance_period(self.ctx, subscription)
            subscriptions.transition(self.ctx, subscription, "canceled")
            if any(
                self.ctx.store.require_price(item["price_id"])["model"] == "metered"
                for item in subscription["items"]
            ):
                final = invoices.build_draft(self.ctx, subscription, ended[1], ended[1], ended)
                finalized = invoices.finalize(self.ctx, final["id"])
                if finalized["status"] == "open":
                    payments.collect(self.ctx, finalized["id"])
                return finalized
            return None
        was_trialing = subscription["status"] == "trialing"
        ended = subscriptions.advance_period(self.ctx, subscription)
        if was_trialing:
            subscriptions.transition(self.ctx, subscription, "active")
            return self._invoice_period(subscription, None)
        return self._invoice_period(subscription, ended)

    def run_cycle(self) -> list[Invoice]:
        """Renew every subscription whose period has ended, retry due payments and send webhooks."""
        now = self.clock.now()
        created: list[Invoice] = []
        renewed = True
        while renewed:
            renewed = False
            for subscription in sorted(self.ctx.store.subscriptions.values(), key=lambda sub: sub["id"]):
                if not subscriptions.is_billable(subscription):
                    continue
                if subscription["current_period_end"] > now:
                    continue
                invoice = self._renew(subscription)
                if invoice is not None:
                    created.append(invoice)
                renewed = True
        for invoice_id in due_for_retry(self.ctx, now):
            payments.collect(self.ctx, invoice_id)
        webhooks.deliver_pending(self.ctx)
        return created

    def report_usage(
        self,
        subscription_id: SubscriptionId,
        price_id: PriceId,
        quantity: int,
        timestamp: EpochMillis | None = None,
        idempotency_key: str | None = None,
    ) -> UsageRecord:
        return usage.report_usage(self.ctx, subscription_id, price_id, quantity, timestamp, idempotency_key)

    def change_plan(self, subscription_id: SubscriptionId, items: list[tuple[PriceId, int]]) -> Subscription:
        return subscriptions.change_items(self.ctx, subscription_id, items)

    def cancel(self, subscription_id: SubscriptionId, *, at_period_end: bool = True) -> Subscription:
        return subscriptions.cancel(self.ctx, subscription_id, at_period_end=at_period_end)

    def apply_coupon(self, subscription_id: SubscriptionId, coupon_id: CouponId) -> Subscription:
        return discounts.redeem(self.ctx, subscription_id, coupon_id)

    def pay(self, invoice_id: InvoiceId) -> Payment | None:
        return payments.collect(self.ctx, invoice_id)

    def refund(self, invoice_id: InvoiceId, reason: str = "requested_by_customer") -> CreditNote:
        return refunds.refund_invoice(self.ctx, invoice_id, reason)

    def invoices_for(self, customer_id: CustomerId) -> list[Invoice]:
        return sorted(self.ctx.store.invoices_for(customer_id), key=lambda inv: (inv["created_at"], inv["id"]))

