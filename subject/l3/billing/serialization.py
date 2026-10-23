"""Conversion of billing records into JSON-compatible payloads."""

import json

from .models import Customer, Invoice, InvoiceLine, JSONObject, JSONValue, Payment, Subscription


def customer_json(customer: Customer) -> JSONObject:
    return {
        "id": customer["id"],
        "email": customer["email"],
        "name": customer["name"],
        "currency": customer["currency"],
        "tax_region": customer["tax_region"],
        "tax_exempt": customer["tax_exempt"],
    }


def line_json(line: InvoiceLine) -> JSONObject:
    return {
        "description": line["description"],
        "kind": line["kind"],
        "price_id": line["price_id"],
        "quantity": line["quantity"],
        "amount": line["amount"],
        "discount_amount": line["discount_amount"],
        "tax_amount": line["tax_amount"],
        "period": [line["period_start"], line["period_end"]],
    }


def invoice_json(invoice: Invoice) -> JSONObject:
    lines: list[JSONValue] = [line_json(line) for line in invoice["lines"]]
    return {
        "id": invoice["id"],
        "number": invoice["number"],
        "customer_id": invoice["customer_id"],
        "subscription_id": invoice["subscription_id"],
        "status": invoice["status"],
        "currency": invoice["currency"],
        "subtotal": invoice["subtotal"],
        "discount_total": invoice["discount_total"],
        "tax_total": invoice["tax_total"],
        "total": invoice["total"],
        "credit_applied": invoice["credit_applied"],
        "amount_due": invoice["amount_due"],
        "amount_paid": invoice["amount_paid"],
        "lines": lines,
    }


def subscription_json(subscription: Subscription) -> JSONObject:
    items: list[JSONValue] = [
        {"price_id": item["price_id"], "quantity": item["quantity"]} for item in subscription["items"]
    ]
    return {
        "id": subscription["id"],
        "customer_id": subscription["customer_id"],
        "status": subscription["status"],
        "items": items,
        "current_period_start": subscription["current_period_start"],
        "current_period_end": subscription["current_period_end"],
        "cancel_at_period_end": subscription["cancel_at_period_end"],
        "coupon_id": subscription["coupon_id"],
    }


def payment_json(payment: Payment) -> JSONObject:
    return {
        "id": payment["id"],
        "invoice_id": payment["invoice_id"],
        "amount": payment["amount"],
        "currency": payment["currency"],
        "status": payment["status"],
        "refunded_amount": payment["refunded_amount"],
    }


def dumps(payload: JSONValue) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
