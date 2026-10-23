"""Invoice drafting, totals and lifecycle.

Invoices move draft -> open -> paid, or end as void or uncollectible. All amounts are
minor units of the invoice currency.
"""

from .catalog import is_metered
from .clock import format_date
from .discounts import applies_this_period, line_discounts
from .errors import CurrencyMismatchError, InvalidStateError, ValidationError
from .events import emit
from .ledger import apply_credit, restore_credit
from .models import INVOICE_NET_DAYS, SECONDS_PER_DAY
from .money import total
from .pricing import amount_for_quantity
from .serialization import invoice_json
from .tax import rate_for_customer, split_tax
from .usage import aggregate_usage


def make_line(description, kind, amount, period_start, period_end, *, price=None, quantity=1, unit_amount=None):
    """Build an invoice line dict with no discount or tax applied yet.

    `kind` is "subscription", "usage", "proration" or "adjustment". `unit_amount`
    defaults to `amount`. The line inherits `tax_inclusive` from `price` when given.
    """
    return {
        "description": description,
        "kind": kind,
        "price_id": price["id"] if price is not None else None,
        "quantity": quantity,
        "unit_amount": unit_amount if unit_amount is not None else amount,
        "amount": amount,
        "discount_amount": 0,
        "tax_rate": 0,
        "tax_amount": 0,
        "tax_inclusive": price["tax_inclusive"] if price is not None else False,
        "period_start": period_start,
        "period_end": period_end,
    }


def _empty_invoice(ctx, customer_id, currency, subscription, period_start, period_end):
    """A new draft invoice with no lines; allocates an invoice id but does not store it."""
    return {
        "id": ctx.ids.invoice(),
        "number": None,
        "customer_id": customer_id,
        "subscription_id": subscription["id"] if subscription is not None else None,
        "status": "draft",
        "currency": currency,
        "lines": [],
        "subtotal": 0,
        "discount_total": 0,
        "tax_total": 0,
        "total": 0,
        "credit_applied": 0,
        "amount_due": 0,
        "amount_paid": 0,
        "attempt_count": 0,
        "next_attempt_at": None,
        "period_start": period_start,
        "period_end": period_end,
        "created_at": ctx.clock.now(),
        "finalized_at": None,
        "due_at": None,
        "paid_at": None,
        "voided_at": None,
    }


def subscription_lines(ctx, subscription, period_start, period_end, usage_period):
    """Licensed items are billed in advance, metered items in arrears.

    Licensed items are charged for [period_start, period_end). Metered items are
    charged for the usage in `usage_period`, a (start, end) pair of epoch seconds, and
    skipped entirely when it is None (for example on the first invoice).
    """
    lines = []
    for item in subscription["items"]:
        price = ctx.store.require_price(item["price_id"])
        plan = ctx.store.require_plan(price["plan_id"])
        if is_metered(price):
            if usage_period is None:
                continue
            usage_start, usage_end = usage_period[0], usage_period[1]
            quantity_used = aggregate_usage(ctx, subscription["id"], price["id"], usage_start, usage_end)
            lines.append(
                make_line(
                    f"{plan['name']} usage ({format_date(usage_start)} - {format_date(usage_end)})",
                    "usage",
                    amount_for_quantity(price, quantity_used),
                    usage_start,
                    usage_end,
                    price=price,
                    quantity=quantity_used,
                    unit_amount=price["unit_amount"],
                )
            )
            continue
        amount = amount_for_quantity(price, item["quantity"])
        lines.append(
            make_line(
                f"{plan['name']} × {item['quantity']}",
                "subscription",
                amount,
                period_start,
                period_end,
                price=price,
                quantity=item["quantity"],
                # Tiered and flat prices have no single unit price; show the floored average.
                unit_amount=amount // item["quantity"] if item["quantity"] else amount,
            )
        )
    return lines


def plan_lookup(ctx, lines):
    """Map each price id referenced by `lines` to its plan id."""
    plan_id_by_price_id = {}
    for line in lines:
        price_id = line["price_id"]
        if price_id is not None:
            plan_id_by_price_id[price_id] = ctx.store.require_price(price_id)["plan_id"]
    return plan_id_by_price_id


def apply_discounts(ctx, invoice, subscription):
    """Reset and recompute each line's `discount_amount` from the subscription's coupon.

    A discount never exceeds its line's amount. Does not update invoice totals.
    """
    for line in invoice["lines"]:
        line["discount_amount"] = 0
    if subscription is None or subscription["coupon_id"] is None:
        return
    coupon = ctx.store.get_coupon(subscription["coupon_id"])
    if coupon is None or not applies_this_period(coupon, subscription["coupon_periods_used"]):
        return
    discounts = line_discounts(coupon, invoice["lines"], invoice["currency"], plan_lookup(ctx, invoice["lines"]))
    for line, discount in zip(invoice["lines"], discounts):
        line["discount_amount"] = min(discount, line["amount"])


def apply_taxes(ctx, invoice):
    """Set each line's `tax_rate` (basis points) and `tax_amount` from the customer's rate.

    Tax is computed on the discounted amount; lines with nothing taxable get zero tax.
    Does not update invoice totals.
    """
    customer = ctx.store.require_customer(invoice["customer_id"])
    rate_bp = rate_for_customer(ctx, customer)
    for line in invoice["lines"]:
        taxable_amount = line["amount"] - line["discount_amount"]
        if taxable_amount <= 0:
            line["tax_rate"], line["tax_amount"] = 0, 0
            continue
        _, tax_amount = split_tax(taxable_amount, rate_bp, line["tax_inclusive"])
        line["tax_rate"] = rate_bp
        line["tax_amount"] = tax_amount


def recompute_totals(invoice):
    """Recalculate subtotal, discount, tax, total and amount due from the lines.

    Inclusive tax is already part of the line amounts, so only exclusive tax is added
    on top. Amount due never goes below zero.
    """
    lines = invoice["lines"]
    invoice["subtotal"] = total(line["amount"] for line in lines)
    invoice["discount_total"] = total(line["discount_amount"] for line in lines)
    invoice["tax_total"] = total(line["tax_amount"] for line in lines)
    exclusive_tax = total(line["tax_amount"] for line in lines if not line["tax_inclusive"])
    invoice["total"] = invoice["subtotal"] - invoice["discount_total"] + exclusive_tax
    invoice["amount_due"] = max(0, invoice["total"] - invoice["credit_applied"] - invoice["amount_paid"])


def build_draft(ctx, subscription, period_start, period_end, usage_period=None):
    """Create, store and return a draft invoice for one subscription period.

    Includes the subscription's items, any pending items (such as prorations), coupon
    discounts and tax, then emits "invoice.created". Drafting consumes one period of
    the subscription's coupon, if it has one.
    """
    customer = ctx.store.require_customer(subscription["customer_id"])
    invoice = _empty_invoice(ctx, customer["id"], customer["currency"], subscription, period_start, period_end)
    invoice["lines"] = subscription_lines(ctx, subscription, period_start, period_end, usage_period)
    invoice["lines"].extend(ctx.store.take_pending_items(subscription["id"]))
    apply_discounts(ctx, invoice, subscription)
    apply_taxes(ctx, invoice)
    recompute_totals(invoice)
    if subscription["coupon_id"] is not None:
        subscription["coupon_periods_used"] += 1
    ctx.store.invoices[invoice["id"]] = invoice
    emit(ctx, "invoice.created", {"invoice": invoice_json(invoice)})
    return invoice


def create_one_off(ctx, customer_id, items):
    """Draft an invoice for ad-hoc charges given as (description, unit amount, quantity).

    Lines are taxed but never discounted. Raises ValidationError if `items` is empty or
    any quantity is below 1.
    """
    if not items:
        raise ValidationError("one-off invoices need at least one item")
    customer = ctx.store.require_customer(customer_id)
    now = ctx.clock.now()
    invoice = _empty_invoice(ctx, customer_id, customer["currency"], None, now, now)
    for description, unit_amount, quantity in items:
        if quantity < 1:
            raise ValidationError("item quantity must be at least 1")
        line = make_line(
            description, "adjustment", unit_amount * quantity, now, now, quantity=quantity, unit_amount=unit_amount
        )
        invoice["lines"].append(line)
    apply_taxes(ctx, invoice)
    recompute_totals(invoice)
    ctx.store.invoices[invoice["id"]] = invoice
    emit(ctx, "invoice.created", {"invoice": invoice_json(invoice)})
    return invoice


def add_pending_item(ctx, subscription, line):
    """Queue a line to be added to the subscription's next invoice.

    Raises CurrencyMismatchError if the line's price is in a different currency than
    the customer.
    """
    customer = ctx.store.require_customer(subscription["customer_id"])
    price_id = line["price_id"]
    if price_id is not None and ctx.store.require_price(price_id)["currency"] != customer["currency"]:
        raise CurrencyMismatchError("pending item currency differs from customer currency")
    ctx.store.pending_items.setdefault(subscription["id"], []).append(line)


def finalize(ctx, invoice_id):
    """Open a draft invoice: assign its number and due date and apply account credit.

    Emits "invoice.finalized"; an invoice that credit fully covers is marked paid right
    away. Raises InvalidStateError unless the invoice is a draft.
    """
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] != "draft":
        raise InvalidStateError(f"only draft invoices can be finalized, got {invoice['status']}")
    now = ctx.clock.now()
    invoice["status"] = "open"
    invoice["number"] = ctx.store.next_invoice_number()
    invoice["finalized_at"] = now
    invoice["due_at"] = now + INVOICE_NET_DAYS * SECONDS_PER_DAY
    apply_credit(ctx, invoice)
    emit(ctx, "invoice.finalized", {"invoice": invoice_json(invoice)})
    if invoice["amount_due"] == 0:
        mark_paid(ctx, invoice, 0)
    return invoice


def mark_paid(ctx, invoice, amount):
    """Record `amount` minor units paid on an open invoice.

    The invoice becomes "paid" (emitting "invoice.paid") once nothing is due. Raises
    InvalidStateError unless the invoice is open.
    """
    if invoice["status"] != "open":
        raise InvalidStateError(f"cannot pay a {invoice['status']} invoice")
    invoice["amount_paid"] = invoice["amount_paid"] + amount
    invoice["amount_due"] = max(0, invoice["amount_due"] - amount)
    if invoice["amount_due"] == 0:
        invoice["status"] = "paid"
        invoice["paid_at"] = ctx.clock.now()
        invoice["next_attempt_at"] = None
        emit(ctx, "invoice.paid", {"invoice": invoice_json(invoice)})


def void(ctx, invoice_id):
    """Cancel a draft or open invoice that has no payments, returning any applied credit.

    Raises InvalidStateError for other statuses or when something was already paid.
    """
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] not in ("draft", "open"):
        raise InvalidStateError(f"cannot void a {invoice['status']} invoice")
    if invoice["amount_paid"] > 0:
        raise InvalidStateError("cannot void an invoice with payments; refund it instead")
    restore_credit(ctx, invoice, "invoice_voided")
    invoice["status"] = "void"
    invoice["voided_at"] = ctx.clock.now()
    invoice["amount_due"] = 0
    invoice["next_attempt_at"] = None
    emit(ctx, "invoice.voided", {"invoice": invoice_json(invoice)})
    return invoice


def mark_uncollectible(ctx, invoice_id):
    """Give up collecting an open invoice; raises InvalidStateError for other statuses."""
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] != "open":
        raise InvalidStateError("only open invoices can be marked uncollectible")
    invoice["status"] = "uncollectible"
    invoice["next_attempt_at"] = None
    emit(ctx, "invoice.marked_uncollectible", {"invoice": invoice_json(invoice)})
    return invoice


def open_invoices(ctx, customer_id):
    return [invoice for invoice in ctx.store.invoices_for(customer_id) if invoice["status"] == "open"]
