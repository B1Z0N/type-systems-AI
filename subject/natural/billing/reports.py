"""Financial reporting over invoices and subscriptions.

Report totals are keyed by currency and never converted between currencies.
"""

from .clock import whole_days_between
from .pricing import amount_for_quantity

# (bucket name, min days overdue, max days overdue or None); invoices not yet due are "current".
AGING_BUCKETS = (("current", -10_000, 0), ("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, None))

# interval -> (numerator, denominator) turning one period's amount into a monthly amount.
MONTHLY_FACTOR = {"day": (365, 12), "week": (52, 12), "month": (1, 1), "year": (1, 12)}


def monthly_amount(ctx, subscription):
    """Recurring licensed revenue of a subscription normalised to one month.

    Metered items are excluded. Each item's monthly share is floored separately.
    """
    monthly_total = 0
    for item in subscription["items"]:
        price = ctx.store.require_price(item["price_id"])
        if price["model"] == "metered":
            continue
        numerator, denominator = MONTHLY_FACTOR[price["interval"]]
        amount_per_period = amount_for_quantity(price, item["quantity"])
        monthly_total += amount_per_period * numerator // (denominator * price["interval_count"])
    return monthly_total


def mrr(ctx):
    """Monthly recurring revenue of active and past-due subscriptions, per customer currency."""
    mrr_by_currency = {}
    for subscription in ctx.store.subscriptions.values():
        if subscription["status"] not in ("active", "past_due"):
            continue
        currency = ctx.store.require_customer(subscription["customer_id"])["currency"]
        mrr_by_currency[currency] = mrr_by_currency.get(currency, 0) + monthly_amount(ctx, subscription)
    return mrr_by_currency


def revenue_between(ctx, start, end):
    """Cash collected on invoices paid within [start, end), net of credit notes.

    `start` and `end` are epoch seconds. Credit notes are subtracted from the invoice
    they belong to, whenever they were issued.
    """
    revenue_by_currency = {}
    for invoice in ctx.store.invoices.values():
        paid_at = invoice["paid_at"]
        if paid_at is None or not start <= paid_at < end:
            continue
        credited_amount = sum(note["amount"] for note in ctx.store.credit_notes_for(invoice["id"]))
        net_amount = invoice["amount_paid"] - credited_amount
        currency = invoice["currency"]
        revenue_by_currency[currency] = revenue_by_currency.get(currency, 0) + net_amount
    return revenue_by_currency


def aging(ctx, now):
    """Open amounts due grouped by days past due date, as {currency: {bucket name: amount}}.

    Only currencies with at least one open invoice appear; all their buckets are present.
    """
    report = {}
    for invoice in ctx.store.invoices.values():
        if invoice["status"] != "open" or invoice["due_at"] is None:
            continue
        days_overdue = whole_days_between(invoice["due_at"], now)
        amount_by_bucket = report.setdefault(invoice["currency"], {name: 0 for name, _, _ in AGING_BUCKETS})
        for name, min_days, max_days in AGING_BUCKETS:
            if days_overdue >= min_days and (max_days is None or days_overdue <= max_days):
                amount_by_bucket[name] = amount_by_bucket[name] + invoice["amount_due"]
                break
    return report


def churn_rate(ctx, start, end):
    """Share of subscriptions alive at start that were canceled before end.

    Returns a float in [0, 1], or 0.0 when no subscription was alive at `start`.
    """
    alive_at_start = [
        subscription
        for subscription in ctx.store.subscriptions.values()
        if subscription["created_at"] < start
        and (subscription["canceled_at"] is None or subscription["canceled_at"] >= start)
    ]
    if not alive_at_start:
        return 0.0
    churned = [
        subscription
        for subscription in alive_at_start
        if subscription["canceled_at"] is not None and subscription["canceled_at"] < end
    ]
    return len(churned) / len(alive_at_start)
