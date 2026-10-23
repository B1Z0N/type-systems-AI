"""High-level facade that wires the billing modules together."""

from . import discounts, invoices, payments, refunds, subscriptions, usage, webhooks
from .context import Context
from .dunning import DEFAULT_POLICY, due_for_retry


class BillingEngine:
    """Entry point for applications: owns a Context and runs the billing cycle.

    `clock` must provide `now()` in epoch seconds. `dunning_policy` defaults to
    dunning.DEFAULT_POLICY. Lower-level operations are available through `self.ctx`.
    """

    def __init__(self, clock, gateway, transport, dunning_policy=None, fx=None):
        self.clock = clock
        self.ctx = Context(clock, gateway, transport, dunning_policy or DEFAULT_POLICY, fx)

    def subscribe(self, customer_id, items, *, trial_days=None, coupon_id=None):
        """Create a subscription and, unless it starts with a trial, invoice the first period."""
        subscription = subscriptions.create(self.ctx, customer_id, items, trial_days=trial_days, coupon_id=coupon_id)
        if subscription["status"] == "active":
            self._invoice_period(subscription, None)
        return subscription

    def _invoice_period(self, subscription, usage_period):
        """Draft, finalize and try to collect the invoice for the current period."""
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

    def _has_metered_items(self, subscription):
        return any(
            self.ctx.store.require_price(item["price_id"])["model"] == "metered" for item in subscription["items"]
        )

    def _renew(self, subscription):
        """Handle a subscription whose period has ended; returns the invoice created, if any."""
        if subscription["cancel_at_period_end"]:
            ended_period = subscriptions.advance_period(self.ctx, subscription)
            subscriptions.transition(self.ctx, subscription, "canceled")
            # Licensed time was paid in advance, but metered usage of the last period is still owed.
            if self._has_metered_items(subscription):
                period_end = ended_period[1]
                final_draft = invoices.build_draft(self.ctx, subscription, period_end, period_end, ended_period)
                final_invoice = invoices.finalize(self.ctx, final_draft["id"])
                if final_invoice["status"] == "open":
                    payments.collect(self.ctx, final_invoice["id"])
                return final_invoice
            return None
        was_trialing = subscription["status"] == "trialing"
        ended_period = subscriptions.advance_period(self.ctx, subscription)
        if was_trialing:
            # Nothing was metered during the trial, so the first paid invoice has no usage lines.
            subscriptions.transition(self.ctx, subscription, "active")
            return self._invoice_period(subscription, None)
        return self._invoice_period(subscription, ended_period)

    def run_cycle(self):
        """Renew every subscription whose period has ended, retry due payments and send webhooks.

        Subscriptions that fell several periods behind are renewed once per missed
        period. Returns the invoices created by renewals (not by payment retries).
        """
        now = self.clock.now()
        created_invoices = []
        renewed_any = True
        while renewed_any:
            renewed_any = False
            for subscription in sorted(self.ctx.store.subscriptions.values(), key=lambda sub: sub["id"]):
                if not subscriptions.is_billable(subscription):
                    continue
                if subscription["current_period_end"] > now:
                    continue
                invoice = self._renew(subscription)
                if invoice is not None:
                    created_invoices.append(invoice)
                renewed_any = True
        for invoice_id in due_for_retry(self.ctx, now):
            payments.collect(self.ctx, invoice_id)
        webhooks.deliver_pending(self.ctx)
        return created_invoices

    def report_usage(self, subscription_id, price_id, quantity, timestamp=None, idempotency_key=None):
        """Record metered usage; `timestamp` is epoch milliseconds. See usage.report_usage."""
        return usage.report_usage(self.ctx, subscription_id, price_id, quantity, timestamp, idempotency_key)

    def change_plan(self, subscription_id, items):
        """Swap the subscription's items with proration. See subscriptions.change_items."""
        return subscriptions.change_items(self.ctx, subscription_id, items)

    def cancel(self, subscription_id, *, at_period_end=True):
        return subscriptions.cancel(self.ctx, subscription_id, at_period_end=at_period_end)

    def apply_coupon(self, subscription_id, coupon_id):
        return discounts.redeem(self.ctx, subscription_id, coupon_id)

    def pay(self, invoice_id):
        """Try to collect an open invoice now; returns the payment, if one was attempted."""
        return payments.collect(self.ctx, invoice_id)

    def refund(self, invoice_id, reason="requested_by_customer"):
        """Fully refund a paid invoice and return the credit note."""
        return refunds.refund_invoice(self.ctx, invoice_id, reason)

    def invoices_for(self, customer_id):
        """The customer's invoices, oldest first."""
        return sorted(self.ctx.store.invoices_for(customer_id), key=lambda invoice: (invoice["created_at"], invoice["id"]))
