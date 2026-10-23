"""Customer accounts and their payment methods."""

import hashlib

from .context import Context
from .errors import InvalidStateError, ValidationError
from .events import emit
from .models import (
    DEFAULT_CURRENCY,
    CurrencyCode,
    Customer,
    CustomerId,
    EpochSeconds,
    PaymentMethod,
    PaymentMethodId,
    PaymentMethodKind,
)
from .money import is_supported
from .serialization import customer_json


def _validate_email(email: str) -> str:
    cleaned = email.strip().lower()
    local, _, domain = cleaned.partition("@")
    if not local or "." not in domain:
        raise ValidationError(f"invalid email: {email!r}")
    return cleaned


def create_customer(
    ctx: Context,
    email: str,
    name: str,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    tax_region: str | None = None,
    tax_exempt: bool = False,
    metadata: dict[str, str] | None = None,
) -> Customer:
    if not is_supported(currency):
        raise ValidationError(f"unsupported currency: {currency}")
    customer: Customer = {
        "id": ctx.ids.customer(),
        "email": _validate_email(email),
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


def update_customer(
    ctx: Context,
    customer_id: CustomerId,
    *,
    email: str | None = None,
    name: str | None = None,
    tax_region: str | None = None,
    tax_exempt: bool | None = None,
) -> Customer:
    customer = ctx.store.require_customer(customer_id)
    if email is not None:
        customer["email"] = _validate_email(email)
    if name is not None:
        customer["name"] = name.strip()
    if tax_region is not None:
        customer["tax_region"] = tax_region.upper() or None
    if tax_exempt is not None:
        customer["tax_exempt"] = tax_exempt
    emit(ctx, "customer.updated", {"customer": customer_json(customer)})
    return customer


def _fingerprint(customer_id: CustomerId, kind: PaymentMethodKind, last4: str, exp_year: int) -> str:
    digest = hashlib.sha256(f"{customer_id}:{kind}:{last4}:{exp_year}".encode()).hexdigest()
    return digest[:16]


def attach_payment_method(
    ctx: Context,
    customer_id: CustomerId,
    kind: PaymentMethodKind,
    last4: str,
    exp_month: int,
    exp_year: int,
    make_default: bool = True,
) -> PaymentMethod:
    customer = ctx.store.require_customer(customer_id)
    if len(last4) != 4 or not last4.isdigit():
        raise ValidationError("last4 must be four digits")
    if not 1 <= exp_month <= 12:
        raise ValidationError("exp_month must be between 1 and 12")
    method: PaymentMethod = {
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


def detach_payment_method(ctx: Context, method_id: PaymentMethodId) -> None:
    method = ctx.store.require_payment_method(method_id)
    customer = ctx.store.require_customer(method["customer_id"])
    del ctx.store.payment_methods[method_id]
    if customer["default_payment_method"] == method_id:
        remaining = ctx.store.payment_methods_for(customer["id"])
        customer["default_payment_method"] = remaining[0]["id"] if remaining else None


def default_payment_method(ctx: Context, customer_id: CustomerId) -> PaymentMethod | None:
    customer = ctx.store.require_customer(customer_id)
    method_id = customer["default_payment_method"]
    if method_id is None:
        return None
    return ctx.store.payment_methods.get(method_id)


def is_expired(method: PaymentMethod, at: EpochSeconds) -> bool:
    from .clock import civil

    moment = civil(at)
    return (method["exp_year"], method["exp_month"]) < (moment.year, moment.month)


def delete_customer(ctx: Context, customer_id: CustomerId) -> None:
    ctx.store.require_customer(customer_id)
    live = [sub for sub in ctx.store.subscriptions_for(customer_id) if sub["status"] != "canceled"]
    if live:
        raise InvalidStateError("customer has live subscriptions")
    open_invoices = [inv for inv in ctx.store.invoices_for(customer_id) if inv["status"] == "open"]
    if open_invoices:
        raise InvalidStateError("customer has open invoices")
    for method in ctx.store.payment_methods_for(customer_id):
        del ctx.store.payment_methods[method["id"]]
    del ctx.store.customers[customer_id]
