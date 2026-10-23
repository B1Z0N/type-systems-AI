"""Plain-text rendering of invoices and account statements."""

from .clock import format_date
from .ledger import balance
from .models import BASIS_POINTS_SCALE
from .money import format_amount

WIDTH = 64


def _row(label, value):
    """Left-aligned label and right-aligned value padded to WIDTH characters."""
    return f"{label:<{WIDTH - len(value) - 1}} {value}"


def format_rate(rate):
    """Basis points as a percentage string: 825 -> "8.25%", 2000 -> "20%"."""
    whole_percent, remainder = divmod(rate * 100, BASIS_POINTS_SCALE)
    hundredths_of_percent = remainder // 100
    if hundredths_of_percent:
        return f"{whole_percent}.{hundredths_of_percent:02d}%"
    return f"{whole_percent}%"


def render_invoice(ctx, invoice):
    """The invoice as fixed-width text, one charge per row, followed by the totals."""
    customer = ctx.store.require_customer(invoice["customer_id"])
    currency = invoice["currency"]
    rows = [
        f"Invoice {invoice['number'] or '(draft)'}",
        f"Billed to: {customer['name']} <{customer['email']}>",
        f"Period: {format_date(invoice['period_start'])} - {format_date(invoice['period_end'])}",
    ]
    if invoice["due_at"] is not None:
        rows.append(f"Due: {format_date(invoice['due_at'])}")
    rows.append("-" * WIDTH)
    for line in invoice["lines"]:
        rows.append(_row(line["description"], format_amount(line["amount"], currency)))
        if line["discount_amount"]:
            rows.append(_row("  discount", format_amount(-line["discount_amount"], currency)))
    rows.append("-" * WIDTH)
    rows.append(_row("Subtotal", format_amount(invoice["subtotal"], currency)))
    if invoice["discount_total"]:
        rows.append(_row("Discounts", format_amount(-invoice["discount_total"], currency)))
    if invoice["tax_total"]:
        taxed_lines = [line for line in invoice["lines"] if line["tax_amount"]]
        rates_bp = sorted({line["tax_rate"] for line in taxed_lines})
        label = "Tax (" + ", ".join(format_rate(rate_bp) for rate_bp in rates_bp) + ")"
        # "incl." only when every taxed line already contains its tax.
        all_inclusive = all(line["tax_inclusive"] for line in taxed_lines)
        rows.append(_row(label + (" incl." if all_inclusive else ""), format_amount(invoice["tax_total"], currency)))
    rows.append(_row("Total", format_amount(invoice["total"], currency)))
    if invoice["credit_applied"]:
        rows.append(_row("Credit applied", format_amount(-invoice["credit_applied"], currency)))
    if invoice["amount_paid"]:
        rows.append(_row("Paid", format_amount(-invoice["amount_paid"], currency)))
    rows.append(_row("Amount due", format_amount(invoice["amount_due"], currency)))
    return "\n".join(rows)


def render_statement(ctx, customer_id):
    """A customer's non-draft invoices, oldest first, with their credit and outstanding balance."""
    customer = ctx.store.require_customer(customer_id)
    currency = customer["currency"]
    invoices = sorted(ctx.store.invoices_for(customer_id), key=lambda invoice: (invoice["created_at"], invoice["id"]))
    rows = [f"Statement for {customer['name']}", "-" * WIDTH]
    for invoice in invoices:
        if invoice["status"] == "draft":
            continue
        label = f"{invoice['number']} {format_date(invoice['created_at'])} [{invoice['status']}]"
        rows.append(_row(label, format_amount(invoice["total"], currency)))
    rows.append("-" * WIDTH)
    rows.append(_row("Account credit", format_amount(balance(ctx, customer_id), currency)))
    outstanding_amount = sum(invoice["amount_due"] for invoice in invoices if invoice["status"] == "open")
    rows.append(_row("Outstanding", format_amount(outstanding_amount, currency)))
    return "\n".join(rows)
