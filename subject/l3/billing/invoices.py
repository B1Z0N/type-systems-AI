"""Invoice drafting, totals and lifecycle."""

from .catalog import is_metered
from .clock import format_date
from .context import Context
from .discounts import applies_this_period, line_discounts
from .errors import CurrencyMismatchError, InvalidStateError, ValidationError
from .events import emit
from .ledger import apply_credit, restore_credit
from .models import (
    INVOICE_NET_DAYS,
    SECONDS_PER_DAY,
    BasisPoints,
    Cents,
    CurrencyCode,
    CustomerId,
    EpochSeconds,
    Invoice,
    InvoiceId,
    InvoiceLine,
    LineKind,
    PlanId,
    Price,
    PriceId,
    Subscription,
)
from .money import total
from .pricing import amount_for_quantity
from .serialization import invoice_json
from .tax import rate_for_customer, split_tax
from .usage import aggregate_usage


def make_line(
    description: str,
    kind: LineKind,
    amount: Cents,
    period_start: EpochSeconds,
    period_end: EpochSeconds,
    *,
    price: Price | None = None,
    quantity: int = 1,
    unit_amount: Cents | None = None,
) -> InvoiceLine:
    return {
        "description": description,
        "kind": kind,
        "price_id": price["id"] if price is not None else None,
        "quantity": quantity,
        "unit_amount": unit_amount if unit_amount is not None else amount,
        "amount": amount,
        "discount_amount": Cents(0),
        "tax_rate": BasisPoints(0),
        "tax_amount": Cents(0),
        "tax_inclusive": price["tax_inclusive"] if price is not None else False,
        "period_start": period_start,
        "period_end": period_end,
    }


def _empty_invoice(
    ctx: Context,
    customer_id: CustomerId,
    currency: CurrencyCode,
    subscription: Subscription | None,
    period_start: EpochSeconds,
    period_end: EpochSeconds,
) -> Invoice:
    return {
        "id": ctx.ids.invoice(),
        "number": None,
        "customer_id": customer_id,
        "subscription_id": subscription["id"] if subscription is not None else None,
        "status": "draft",
        "currency": currency,
        "lines": [],
        "subtotal": Cents(0),
        "discount_total": Cents(0),
        "tax_total": Cents(0),
        "total": Cents(0),
        "credit_applied": Cents(0),
        "amount_due": Cents(0),
        "amount_paid": Cents(0),
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


def subscription_lines(
    ctx: Context,
    subscription: Subscription,
    period_start: EpochSeconds,
    period_end: EpochSeconds,
    usage_period: tuple[EpochSeconds, EpochSeconds] | None,
) -> list[InvoiceLine]:
    """Licensed items are billed in advance, metered items in arrears."""
    lines: list[InvoiceLine] = []
    for item in subscription["items"]:
        price = ctx.store.require_price(item["price_id"])
        plan = ctx.store.require_plan(price["plan_id"])
        if is_metered(price):
            if usage_period is None:
                continue
            used = aggregate_usage(ctx, subscription["id"], price["id"], usage_period[0], usage_period[1])
            lines.append(
                make_line(
                    f"{plan['name']} usage ({format_date(usage_period[0])} - {format_date(usage_period[1])})",
                    "usage",
                    amount_for_quantity(price, used),
                    usage_period[0],
                    usage_period[1],
                    price=price,
                    quantity=used,
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
                unit_amount=Cents(amount // item["quantity"]) if item["quantity"] else amount,
            )
        )
    return lines


def plan_lookup(ctx: Context, lines: list[InvoiceLine]) -> dict[PriceId, PlanId]:
    plan_of: dict[PriceId, PlanId] = {}
    for line in lines:
        price_id = line["price_id"]
        if price_id is not None:
            plan_of[price_id] = ctx.store.require_price(price_id)["plan_id"]
    return plan_of


def apply_discounts(ctx: Context, invoice: Invoice, subscription: Subscription | None) -> None:
    for line in invoice["lines"]:
        line["discount_amount"] = Cents(0)
    if subscription is None or subscription["coupon_id"] is None:
        return
    coupon = ctx.store.get_coupon(subscription["coupon_id"])
    if coupon is None or not applies_this_period(coupon, subscription["coupon_periods_used"]):
        return
    discounts = line_discounts(coupon, invoice["lines"], invoice["currency"], plan_lookup(ctx, invoice["lines"]))
    for line, discount in zip(invoice["lines"], discounts):
        line["discount_amount"] = Cents(min(discount, line["amount"]))


def apply_taxes(ctx: Context, invoice: Invoice) -> None:
    customer = ctx.store.require_customer(invoice["customer_id"])
    rate = rate_for_customer(ctx, customer)
    for line in invoice["lines"]:
        taxable = Cents(line["amount"] - line["discount_amount"])
        if taxable <= 0:
            line["tax_rate"], line["tax_amount"] = BasisPoints(0), Cents(0)
            continue
        _, tax = split_tax(taxable, rate, line["tax_inclusive"])
        line["tax_rate"] = rate
        line["tax_amount"] = tax


def recompute_totals(invoice: Invoice) -> None:
    lines = invoice["lines"]
    invoice["subtotal"] = total(line["amount"] for line in lines)
    invoice["discount_total"] = total(line["discount_amount"] for line in lines)
    invoice["tax_total"] = total(line["tax_amount"] for line in lines)
    exclusive_tax = total(line["tax_amount"] for line in lines if not line["tax_inclusive"])
    invoice["total"] = Cents(invoice["subtotal"] - invoice["discount_total"] + exclusive_tax)
    invoice["amount_due"] = Cents(max(0, invoice["total"] - invoice["credit_applied"] - invoice["amount_paid"]))


def build_draft(
    ctx: Context,
    subscription: Subscription,
    period_start: EpochSeconds,
    period_end: EpochSeconds,
    usage_period: tuple[EpochSeconds, EpochSeconds] | None = None,
) -> Invoice:
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


def create_one_off(
    ctx: Context, customer_id: CustomerId, items: list[tuple[str, Cents, int]]
) -> Invoice:
    """Draft an invoice for ad-hoc charges given as (description, unit amount, quantity)."""
    if not items:
        raise ValidationError("one-off invoices need at least one item")
    customer = ctx.store.require_customer(customer_id)
    now = ctx.clock.now()
    invoice = _empty_invoice(ctx, customer_id, customer["currency"], None, now, now)
    for description, unit_amount, quantity in items:
        if quantity < 1:
            raise ValidationError("item quantity must be at least 1")
        line = make_line(description, "adjustment", Cents(unit_amount * quantity), now, now, quantity=quantity, unit_amount=unit_amount)
        invoice["lines"].append(line)
    apply_taxes(ctx, invoice)
    recompute_totals(invoice)
    ctx.store.invoices[invoice["id"]] = invoice
    emit(ctx, "invoice.created", {"invoice": invoice_json(invoice)})
    return invoice


def add_pending_item(ctx: Context, subscription: Subscription, line: InvoiceLine) -> None:
    customer = ctx.store.require_customer(subscription["customer_id"])
    price_id = line["price_id"]
    if price_id is not None and ctx.store.require_price(price_id)["currency"] != customer["currency"]:
        raise CurrencyMismatchError("pending item currency differs from customer currency")
    ctx.store.pending_items.setdefault(subscription["id"], []).append(line)


def finalize(ctx: Context, invoice_id: InvoiceId) -> Invoice:
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] != "draft":
        raise InvalidStateError(f"only draft invoices can be finalized, got {invoice['status']}")
    now = ctx.clock.now()
    invoice["status"] = "open"
    invoice["number"] = ctx.store.next_invoice_number()
    invoice["finalized_at"] = now
    invoice["due_at"] = EpochSeconds(now + INVOICE_NET_DAYS * SECONDS_PER_DAY)
    apply_credit(ctx, invoice)
    emit(ctx, "invoice.finalized", {"invoice": invoice_json(invoice)})
    if invoice["amount_due"] == 0:
        mark_paid(ctx, invoice, Cents(0))
    return invoice


def mark_paid(ctx: Context, invoice: Invoice, amount: Cents) -> None:
    if invoice["status"] != "open":
        raise InvalidStateError(f"cannot pay a {invoice['status']} invoice")
    invoice["amount_paid"] = Cents(invoice["amount_paid"] + amount)
    invoice["amount_due"] = Cents(max(0, invoice["amount_due"] - amount))
    if invoice["amount_due"] == 0:
        invoice["status"] = "paid"
        invoice["paid_at"] = ctx.clock.now()
        invoice["next_attempt_at"] = None
        emit(ctx, "invoice.paid", {"invoice": invoice_json(invoice)})


def void(ctx: Context, invoice_id: InvoiceId) -> Invoice:
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] not in ("draft", "open"):
        raise InvalidStateError(f"cannot void a {invoice['status']} invoice")
    if invoice["amount_paid"] > 0:
        raise InvalidStateError("cannot void an invoice with payments; refund it instead")
    restore_credit(ctx, invoice, "invoice_voided")
    invoice["status"] = "void"
    invoice["voided_at"] = ctx.clock.now()
    invoice["amount_due"] = Cents(0)
    invoice["next_attempt_at"] = None
    emit(ctx, "invoice.voided", {"invoice": invoice_json(invoice)})
    return invoice


def mark_uncollectible(ctx: Context, invoice_id: InvoiceId) -> Invoice:
    invoice = ctx.store.require_invoice(invoice_id)
    if invoice["status"] != "open":
        raise InvalidStateError("only open invoices can be marked uncollectible")
    invoice["status"] = "uncollectible"
    invoice["next_attempt_at"] = None
    emit(ctx, "invoice.marked_uncollectible", {"invoice": invoice_json(invoice)})
    return invoice


def open_invoices(ctx: Context, customer_id: CustomerId) -> list[Invoice]:
    return [inv for inv in ctx.store.invoices_for(customer_id) if inv["status"] == "open"]
