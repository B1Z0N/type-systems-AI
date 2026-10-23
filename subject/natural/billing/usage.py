"""Metered usage reporting and aggregation.

Usage record timestamps are epoch milliseconds, while billing periods are epoch seconds.
"""

from .clock import to_millis
from .errors import ValidationError


def report_usage(ctx, subscription_id, price_id, quantity, timestamp=None, idempotency_key=None):
    """Record `quantity` units of metered usage and return the usage record.

    `timestamp` is epoch milliseconds and defaults to now. When `idempotency_key` was
    seen before, the original record is returned unchanged and nothing is validated.
    Raises ValidationError if the subscription is canceled or paused, the price is not
    metered or not on the subscription, or the quantity is negative.
    """
    if idempotency_key is not None and idempotency_key in ctx.store.usage_keys:
        return ctx.store.usage_records[ctx.store.usage_keys[idempotency_key]]
    subscription = ctx.store.require_subscription(subscription_id)
    if subscription["status"] in ("canceled", "paused"):
        raise ValidationError(f"cannot report usage on a {subscription['status']} subscription")
    price = ctx.store.require_price(price_id)
    if price["model"] != "metered":
        raise ValidationError("usage can only be reported for metered prices")
    if all(item["price_id"] != price_id for item in subscription["items"]):
        raise ValidationError("price is not part of the subscription")
    if quantity < 0:
        raise ValidationError("usage quantity cannot be negative")
    record = {
        "id": ctx.ids.usage_record(),
        "subscription_id": subscription_id,
        "price_id": price_id,
        "quantity": quantity,
        "timestamp": timestamp if timestamp is not None else to_millis(ctx.clock.now()),
        "idempotency_key": idempotency_key,
    }
    ctx.store.usage_records[record["id"]] = record
    if idempotency_key is not None:
        ctx.store.usage_keys[idempotency_key] = record["id"]
    return record


def records_in_period(ctx, subscription_id, price_id, period_start, period_end):
    """Usage records within [period_start, period_end) (epoch seconds), oldest first."""
    start_ms = to_millis(period_start)
    end_ms = to_millis(period_end)
    records = [
        record for record in ctx.store.usage_for(subscription_id, price_id) if start_ms <= record["timestamp"] < end_ms
    ]
    return sorted(records, key=lambda record: (record["timestamp"], record["id"]))


def aggregate_usage(ctx, subscription_id, price_id, period_start, period_end):
    """Billable quantity for the period using the price's aggregation.

    "max" takes the largest report, "last" the most recent one and anything else
    (normally "sum") adds them up. Returns 0 when nothing was reported.
    """
    price = ctx.store.require_price(price_id)
    records = records_in_period(ctx, subscription_id, price_id, period_start, period_end)
    if not records:
        return 0
    aggregation = price["usage_aggregation"]
    if aggregation == "max":
        return max(record["quantity"] for record in records)
    if aggregation == "last":
        return records[-1]["quantity"]
    return sum(record["quantity"] for record in records)


def usage_summary(ctx, subscription_id, period_start, period_end):
    """Map of metered price id to aggregated quantity for the subscription's period."""
    subscription = ctx.store.require_subscription(subscription_id)
    quantity_by_price_id = {}
    for item in subscription["items"]:
        price = ctx.store.require_price(item["price_id"])
        if price["model"] == "metered":
            quantity_by_price_id[price["id"]] = aggregate_usage(
                ctx, subscription_id, price["id"], period_start, period_end
            )
    return quantity_by_price_id
