"""Plain-text rendering of invoices and account statements."""

from .clock import format_date
from .context import Context
from .ledger import balance
from .models import BASIS_POINTS_SCALE, BasisPoints, Cents, CustomerId, Invoice
from .money import format_amount

WIDTH = 64


def _row(label: str, value: str) -> str:
    return f"{label:<{WIDTH - len(value) - 1}} {value}"


def format_rate(rate: BasisPoints) -> str:
    whole, fraction = divmod(rate * 100, BASIS_POINTS_SCALE)
    fraction //= 100
    return f"{whole}.{fraction:02d}%" if fraction else f"{whole}%"


def render_invoice(ctx: Context, invoice: Invoice) -> str:
    customer = ctx.store.require_customer(invoice["customer_id"])
    currency = invoice["currency"]
    out = [
        f"Invoice {invoice['number'] or '(draft)'}",
        f"Billed to: {customer['name']} <{customer['email']}>",
        f"Period: {format_date(invoice['period_start'])} - {format_date(invoice['period_end'])}",
    ]
    if invoice["due_at"] is not None:
        out.append(f"Due: {format_date(invoice['due_at'])}")
    out.append("-" * WIDTH)
    for line in invoice["lines"]:
        out.append(_row(line["description"], format_amount(line["amount"], currency)))
        if line["discount_amount"]:
            out.append(_row("  discount", format_amount(Cents(-line["discount_amount"]), currency)))
    out.append("-" * WIDTH)
    out.append(_row("Subtotal", format_amount(invoice["subtotal"], currency)))
    if invoice["discount_total"]:
        out.append(_row("Discounts", format_amount(Cents(-invoice["discount_total"]), currency)))
    if invoice["tax_total"]:
        rates = sorted({line["tax_rate"] for line in invoice["lines"] if line["tax_amount"]})
        label = "Tax (" + ", ".join(format_rate(rate) for rate in rates) + ")"
        inclusive = all(line["tax_inclusive"] for line in invoice["lines"] if line["tax_amount"])
        out.append(_row(label + (" incl." if inclusive else ""), format_amount(invoice["tax_total"], currency)))
    out.append(_row("Total", format_amount(invoice["total"], currency)))
    if invoice["credit_applied"]:
        out.append(_row("Credit applied", format_amount(Cents(-invoice["credit_applied"]), currency)))
    if invoice["amount_paid"]:
        out.append(_row("Paid", format_amount(Cents(-invoice["amount_paid"]), currency)))
    out.append(_row("Amount due", format_amount(invoice["amount_due"], currency)))
    return "\n".join(out)


def render_statement(ctx: Context, customer_id: CustomerId) -> str:
    customer = ctx.store.require_customer(customer_id)
    currency = customer["currency"]
    invoices = sorted(ctx.store.invoices_for(customer_id), key=lambda inv: (inv["created_at"], inv["id"]))
    out = [f"Statement for {customer['name']}", "-" * WIDTH]
    for invoice in invoices:
        if invoice["status"] == "draft":
            continue
        label = f"{invoice['number']} {format_date(invoice['created_at'])} [{invoice['status']}]"
        out.append(_row(label, format_amount(invoice["total"], currency)))
    out.append("-" * WIDTH)
    out.append(_row("Account credit", format_amount(balance(ctx, customer_id), currency)))
    outstanding = Cents(sum(inv["amount_due"] for inv in invoices if inv["status"] == "open"))
    out.append(_row("Outstanding", format_amount(outstanding, currency)))
    return "\n".join(out)
