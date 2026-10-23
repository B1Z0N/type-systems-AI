"""Metered usage reporting and aggregation."""

from .clock import to_millis
from .context import Context
from .errors import ValidationError
from .models import EpochMillis, EpochSeconds, PriceId, SubscriptionId, UsageRecord


def report_usage(
    ctx: Context,
    subscription_id: SubscriptionId,
    price_id: PriceId,
    quantity: int,
    timestamp: EpochMillis | None = None,
    idempotency_key: str | None = None,
) -> UsageRecord:
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
    record: UsageRecord = {
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


def records_in_period(
    ctx: Context,
    subscription_id: SubscriptionId,
    price_id: PriceId,
    period_start: EpochSeconds,
    period_end: EpochSeconds,
) -> list[UsageRecord]:
    start = to_millis(period_start)
    end = to_millis(period_end)
    records = [
        record
        for record in ctx.store.usage_for(subscription_id, price_id)
        if start <= record["timestamp"] < end
    ]
    return sorted(records, key=lambda record: (record["timestamp"], record["id"]))


def aggregate_usage(
    ctx: Context,
    subscription_id: SubscriptionId,
    price_id: PriceId,
    period_start: EpochSeconds,
    period_end: EpochSeconds,
) -> int:
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


def usage_summary(
    ctx: Context, subscription_id: SubscriptionId, period_start: EpochSeconds, period_end: EpochSeconds
) -> dict[PriceId, int]:
    subscription = ctx.store.require_subscription(subscription_id)
    summary: dict[PriceId, int] = {}
    for item in subscription["items"]:
        price = ctx.store.require_price(item["price_id"])
        if price["model"] == "metered":
            summary[price["id"]] = aggregate_usage(ctx, subscription_id, price["id"], period_start, period_end)
    return summary
