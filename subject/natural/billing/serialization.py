"""Conversion of billing records into JSON-compatible payloads.

These shapes are part of the public API and webhook contract; only listed fields are
exposed.
"""

import json


def customer_json(customer):
    return {
        "id": customer["id"],
        "email": customer["email"],
        "name": customer["name"],
        "currency": customer["currency"],
        "tax_region": customer["tax_region"],
        "tax_exempt": customer["tax_exempt"],
    }


def line_json(line):
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


def invoice_json(invoice):
    lines = [line_json(line) for line in invoice["lines"]]
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


def subscription_json(subscription):
    items = [{"price_id": item["price_id"], "quantity": item["quantity"]} for item in subscription["items"]]
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


def payment_json(payment):
    return {
        "id": payment["id"],
        "invoice_id": payment["invoice_id"],
        "amount": payment["amount"],
        "currency": payment["currency"],
        "status": payment["status"],
        "refunded_amount": payment["refunded_amount"],
    }


def dumps(payload):
    """Canonical compact JSON as UTF-8 bytes; key order is fixed so signatures are stable."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
