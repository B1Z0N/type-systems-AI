"""Credits and charges when a subscription changes mid-period."""

from .catalog import is_metered
from .context import Context
from .invoices import make_line
from .models import Cents, EpochSeconds, InvoiceLine, Subscription, SubscriptionItem
from .money import prorate
from .pricing import amount_for_quantity


def proration_lines(
    ctx: Context,
    subscription: Subscription,
    old_items: list[SubscriptionItem],
    new_items: list[SubscriptionItem],
    change_at: EpochSeconds,
) -> list[InvoiceLine]:
    start = subscription["current_period_start"]
    end = subscription["current_period_end"]
    if not start <= change_at < end:
        return []
    period = end - start
    remaining = end - change_at
    lines: list[InvoiceLine] = []
    for item in old_items:
        price = ctx.store.require_price(item["price_id"])
        if is_metered(price):
            continue
        plan = ctx.store.require_plan(price["plan_id"])
        credit = prorate(amount_for_quantity(price, item["quantity"]), remaining, period)
        if credit:
            lines.append(
                make_line(
                    f"Unused time on {plan['name']} × {item['quantity']}",
                    "proration",
                    Cents(-credit),
                    change_at,
                    end,
                    price=price,
                    quantity=item["quantity"],
                )
            )
    for item in new_items:
        price = ctx.store.require_price(item["price_id"])
        if is_metered(price):
            continue
        plan = ctx.store.require_plan(price["plan_id"])
        charge = prorate(amount_for_quantity(price, item["quantity"]), remaining, period)
        if charge:
            lines.append(
                make_line(
                    f"Remaining time on {plan['name']} × {item['quantity']}",
                    "proration",
                    charge,
                    change_at,
                    end,
                    price=price,
                    quantity=item["quantity"],
                )
            )
    return lines


def net_proration(lines: list[InvoiceLine]) -> Cents:
    return Cents(sum(line["amount"] for line in lines))
