"""Credits and charges when a subscription changes mid-period."""

from .catalog import is_metered
from .invoices import make_line
from .money import prorate
from .pricing import amount_for_quantity


def proration_lines(ctx, subscription, old_items, new_items, change_at):
    """Invoice lines that settle a mid-period switch from `old_items` to `new_items`.

    Licensed items are billed in advance, so each old item gets a negative line
    crediting the unused remainder of the current period and each new item a positive
    line charging for it. Metered items are skipped (they are billed in arrears) and so
    are zero-amount lines. Returns [] unless `change_at` (epoch seconds) falls inside
    the current period.
    """
    period_start = subscription["current_period_start"]
    period_end = subscription["current_period_end"]
    if not period_start <= change_at < period_end:
        return []
    period_seconds = period_end - period_start
    remaining_seconds = period_end - change_at
    lines = []
    for item in old_items:
        price = ctx.store.require_price(item["price_id"])
        if is_metered(price):
            continue
        plan = ctx.store.require_plan(price["plan_id"])
        credit = prorate(amount_for_quantity(price, item["quantity"]), remaining_seconds, period_seconds)
        if credit:
            lines.append(
                make_line(
                    f"Unused time on {plan['name']} × {item['quantity']}",
                    "proration",
                    -credit,
                    change_at,
                    period_end,
                    price=price,
                    quantity=item["quantity"],
                )
            )
    for item in new_items:
        price = ctx.store.require_price(item["price_id"])
        if is_metered(price):
            continue
        plan = ctx.store.require_plan(price["plan_id"])
        charge = prorate(amount_for_quantity(price, item["quantity"]), remaining_seconds, period_seconds)
        if charge:
            lines.append(
                make_line(
                    f"Remaining time on {plan['name']} × {item['quantity']}",
                    "proration",
                    charge,
                    change_at,
                    period_end,
                    price=price,
                    quantity=item["quantity"],
                )
            )
    return lines


def net_proration(lines):
    """Sum of the line amounts; negative means the customer is owed money."""
    return sum(line["amount"] for line in lines)
