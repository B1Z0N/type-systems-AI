"""Customer accounts and their payment methods."""

import hashlib

from .errors import InvalidStateError, ValidationError
from .events import emit
from .models import DEFAULT_CURRENCY
from .money import is_supported
from .serialization import customer_json


def _normalize_email(email):
    """Return the trimmed, lower-cased email; raises ValidationError if it looks invalid.

    The check is deliberately loose: a non-empty local part and a dotted domain.
    """
    normalized = email.strip().lower()
    local_part, _, domain = normalized.partition("@")
    if not local_part or "." not in domain:
        raise ValidationError(f"invalid email: {email!r}")
    return normalized


def create_customer(ctx, email, name, currency=DEFAULT_CURRENCY, tax_region=None, tax_exempt=False, metadata=None):
    """Create a customer billed in `currency` and emit "customer.created".

    `tax_region` is a region code such as "US-CA" (stored upper-case) used for tax
    lookup; `metadata` is copied. Raises ValidationError for an unsupported currency or
    a malformed email.
    """
    if not is_supported(currency):
        raise ValidationError(f"unsupported currency: {currency}")
    # The id is allocated before the email is validated, so a rejected email still
    # consumes an id. Keep this order: ids must stay stable across versions.
    customer = {
        "id": ctx.ids.customer(),
        "email": _normalize_email(email),
        "name": name.strip(),
        "currency": currency,
        "tax_region": tax_region.upper() if tax_region else None,
        "tax_exempt": tax_exempt,
        "default_payment_method": None,
        "created_at": ctx.clock.now(),
        "metadata": dict(metadata or {}),
    }
    ctx.store.customers[customer["id"]] = customer
    emit(ctx, "customer.created", {"customer": customer_json(customer)})
    return customer


def update_customer(ctx, customer_id, *, email=None, name=None, tax_region=None, tax_exempt=None):
    """Update the given fields (None means "leave unchanged") and emit "customer.updated".

    Passing `tax_region=""` clears the region. Fields are applied one by one, so an
    invalid email raises ValidationError without touching the other fields.
    """
    customer = ctx.store.require_customer(customer_id)
    if email is not None:
        customer["email"] = _normalize_email(email)
    if name is not None:
        customer["name"] = name.strip()
    if tax_region is not None:
        customer["tax_region"] = tax_region.upper() or None
    if tax_exempt is not None:
        customer["tax_exempt"] = tax_exempt
    emit(ctx, "customer.updated", {"customer": customer_json(customer)})
    return customer


def _fingerprint(customer_id, kind, last4, exp_year):
    """Short stable hash identifying the same card details for the same customer."""
    digest = hashlib.sha256(f"{customer_id}:{kind}:{last4}:{exp_year}".encode()).hexdigest()
    return digest[:16]


def attach_payment_method(ctx, customer_id, kind, last4, exp_month, exp_year, make_default=True):
    """Store a payment method for the customer and return it.

    `last4` is a four-digit string and `exp_month` is 1-12. The method becomes the
    default if `make_default` is true or the customer has no default yet.
    """
    customer = ctx.store.require_customer(customer_id)
    if len(last4) != 4 or not last4.isdigit():
        raise ValidationError("last4 must be four digits")
    if not 1 <= exp_month <= 12:
        raise ValidationError("exp_month must be between 1 and 12")
    method = {
        "id": ctx.ids.payment_method(),
        "customer_id": customer_id,
        "kind": kind,
        "last4": last4,
        "exp_month": exp_month,
        "exp_year": exp_year,
        "fingerprint": _fingerprint(customer_id, kind, last4, exp_year),
    }
    ctx.store.payment_methods[method["id"]] = method
    if make_default or customer["default_payment_method"] is None:
        customer["default_payment_method"] = method["id"]
    return method


def detach_payment_method(ctx, method_id):
    """Delete a payment method; if it was the default, the oldest remaining one takes over."""
    method = ctx.store.require_payment_method(method_id)
    customer = ctx.store.require_customer(method["customer_id"])
    del ctx.store.payment_methods[method_id]
    if customer["default_payment_method"] == method_id:
        remaining_methods = ctx.store.payment_methods_for(customer["id"])
        customer["default_payment_method"] = remaining_methods[0]["id"] if remaining_methods else None


def default_payment_method(ctx, customer_id):
    """The customer's default payment method record, or None if there is none."""
    customer = ctx.store.require_customer(customer_id)
    method_id = customer["default_payment_method"]
    if method_id is None:
        return None
    return ctx.store.payment_methods.get(method_id)


def is_expired(method, at):
    """Whether the card has expired by epoch second `at`.

    A card stays valid through the whole of its expiry month.
    """
    from .clock import civil

    moment = civil(at)
    return (method["exp_year"], method["exp_month"]) < (moment.year, moment.month)


def delete_customer(ctx, customer_id):
    """Delete a customer and their payment methods.

    Raises InvalidStateError while the customer still has a non-canceled subscription
    or an open invoice.
    """
    ctx.store.require_customer(customer_id)
    live_subscriptions = [
        subscription
        for subscription in ctx.store.subscriptions_for(customer_id)
        if subscription["status"] != "canceled"
    ]
    if live_subscriptions:
        raise InvalidStateError("customer has live subscriptions")
    open_invoices = [invoice for invoice in ctx.store.invoices_for(customer_id) if invoice["status"] == "open"]
    if open_invoices:
        raise InvalidStateError("customer has open invoices")
    for method in ctx.store.payment_methods_for(customer_id):
        del ctx.store.payment_methods[method["id"]]
    del ctx.store.customers[customer_id]
