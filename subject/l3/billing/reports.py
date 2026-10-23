"""Financial reporting over invoices and subscriptions."""

from typing import Final

from .clock import whole_days_between
from .context import Context
from .models import Cents, CurrencyCode, EpochSeconds, Interval, Subscription
from .pricing import amount_for_quantity

AGING_BUCKETS: Final[tuple[tuple[str, int, int | None], ...]] = (
    ("current", -10_000, 0),
    ("1-30", 1, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("90+", 91, None),
)

MONTHLY_FACTOR: Final[dict[Interval, tuple[int, int]]] = {
    "day": (365, 12),
    "week": (52, 12),
    "month": (1, 1),
    "year": (1, 12),
}


def monthly_amount(ctx: Context, subscription: Subscription) -> Cents:
    """Recurring licensed revenue of a subscription normalised to one month."""
    amount = 0
    for item in subscription["items"]:
        price = ctx.store.require_price(item["price_id"])
        if price["model"] == "metered":
            continue
        numerator, denominator = MONTHLY_FACTOR[price["interval"]]
        per_period = amount_for_quantity(price, item["quantity"])
        amount += per_period * numerator // (denominator * price["interval_count"])
    return Cents(amount)


def mrr(ctx: Context) -> dict[CurrencyCode, Cents]:
    totals: dict[CurrencyCode, Cents] = {}
    for subscription in ctx.store.subscriptions.values():
        if subscription["status"] not in ("active", "past_due"):
            continue
        currency = ctx.store.require_customer(subscription["customer_id"])["currency"]
        totals[currency] = Cents(totals.get(currency, Cents(0)) + monthly_amount(ctx, subscription))
    return totals


def revenue_between(ctx: Context, start: EpochSeconds, end: EpochSeconds) -> dict[CurrencyCode, Cents]:
    """Cash collected on invoices paid within [start, end), net of credit notes."""
    totals: dict[CurrencyCode, Cents] = {}
    for invoice in ctx.store.invoices.values():
        paid_at = invoice["paid_at"]
        if paid_at is None or not start <= paid_at < end:
            continue
        credited = sum(note["amount"] for note in ctx.store.credit_notes_for(invoice["id"]))
        net = invoice["amount_paid"] - credited
        currency = invoice["currency"]
        totals[currency] = Cents(totals.get(currency, Cents(0)) + net)
    return totals


def aging(ctx: Context, now: EpochSeconds) -> dict[CurrencyCode, dict[str, Cents]]:
    report: dict[CurrencyCode, dict[str, Cents]] = {}
    for invoice in ctx.store.invoices.values():
        if invoice["status"] != "open" or invoice["due_at"] is None:
            continue
        overdue = whole_days_between(invoice["due_at"], now)
        buckets = report.setdefault(invoice["currency"], {name: Cents(0) for name, _, _ in AGING_BUCKETS})
        for name, low, high in AGING_BUCKETS:
            if overdue >= low and (high is None or overdue <= high):
                buckets[name] = Cents(buckets[name] + invoice["amount_due"])
                break
    return report


def churn_rate(ctx: Context, start: EpochSeconds, end: EpochSeconds) -> float:
    """Share of subscriptions alive at start that were canceled before end."""
    alive = [
        sub
        for sub in ctx.store.subscriptions.values()
        if sub["created_at"] < start and (sub["canceled_at"] is None or sub["canceled_at"] >= start)
    ]
    if not alive:
        return 0.0
    churned = [sub for sub in alive if sub["canceled_at"] is not None and sub["canceled_at"] < end]
    return len(churned) / len(alive)
