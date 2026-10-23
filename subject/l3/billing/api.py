"""JSON request handling for the public billing API."""

from collections.abc import Callable
from typing import Final

from . import customers, invoices
from .engine import BillingEngine
from .errors import BillingError, NotFoundError, ValidationError
from .models import (
    CurrencyCode,
    CustomerId,
    EpochMillis,
    InvoiceId,
    JSONObject,
    JSONValue,
    PriceId,
    SubscriptionId,
)
from .render import render_invoice
from .serialization import customer_json, invoice_json, subscription_json

Response = tuple[int, JSONObject]
Handler = Callable[[BillingEngine, list[str], JSONObject], Response]


def _str(body: JSONObject, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{key} must be a non-empty string")
    return value


def _optional_str(body: JSONObject, key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be a string")
    return value


def _int(body: JSONObject, key: str, default: int | None = None) -> int:
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{key} must be an integer")
    return value


def _items(body: JSONObject) -> list[tuple[PriceId, int]]:
    raw = body.get("items")
    if not isinstance(raw, list) or not raw:
        raise ValidationError("items must be a non-empty list")
    items: list[tuple[PriceId, int]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValidationError("each item must be an object")
        items.append((PriceId(_str(entry, "price")), _int(entry, "quantity", 1)))
    return items


def create_customer(engine: BillingEngine, _: list[str], body: JSONObject) -> Response:
    customer = customers.create_customer(
        engine.ctx,
        _str(body, "email"),
        _str(body, "name"),
        CurrencyCode(_optional_str(body, "currency") or "usd"),
        tax_region=_optional_str(body, "tax_region"),
    )
    return 201, customer_json(customer)


def create_subscription(engine: BillingEngine, _: list[str], body: JSONObject) -> Response:
    trial = body.get("trial_days")
    subscription = engine.subscribe(
        CustomerId(_str(body, "customer")),
        _items(body),
        trial_days=_int(body, "trial_days") if trial is not None else None,
    )
    return 201, subscription_json(subscription)


def report_usage(engine: BillingEngine, _: list[str], body: JSONObject) -> Response:
    """Usage timestamps arrive as epoch milliseconds, matching the client SDKs."""
    raw_timestamp = body.get("timestamp")
    timestamp = EpochMillis(_int(body, "timestamp")) if raw_timestamp is not None else None
    record = engine.report_usage(
        SubscriptionId(_str(body, "subscription")),
        PriceId(_str(body, "price")),
        _int(body, "quantity"),
        timestamp,
        _optional_str(body, "idempotency_key"),
    )
    return 201, {"id": record["id"], "quantity": record["quantity"], "timestamp": record["timestamp"]}


def get_invoice(engine: BillingEngine, params: list[str], _: JSONObject) -> Response:
    invoice = engine.ctx.store.require_invoice(InvoiceId(params[0]))
    return 200, invoice_json(invoice)


def get_invoice_text(engine: BillingEngine, params: list[str], _: JSONObject) -> Response:
    invoice = engine.ctx.store.require_invoice(InvoiceId(params[0]))
    return 200, {"text": render_invoice(engine.ctx, invoice)}


def refund_invoice(engine: BillingEngine, params: list[str], body: JSONObject) -> Response:
    note = engine.refund(InvoiceId(params[0]), _optional_str(body, "reason") or "requested_by_customer")
    return 200, {"credit_note": note["id"], "amount": note["amount"]}


def void_invoice(engine: BillingEngine, params: list[str], _: JSONObject) -> Response:
    invoice = invoices.void(engine.ctx, InvoiceId(params[0]))
    return 200, invoice_json(invoice)


def cancel_subscription(engine: BillingEngine, params: list[str], body: JSONObject) -> Response:
    at_period_end = body.get("at_period_end", True)
    if not isinstance(at_period_end, bool):
        raise ValidationError("at_period_end must be a boolean")
    subscription = engine.cancel(SubscriptionId(params[0]), at_period_end=at_period_end)
    return 200, subscription_json(subscription)


ROUTES: Final[list[tuple[str, tuple[str, ...], Handler]]] = [
    ("POST", ("customers",), create_customer),
    ("POST", ("subscriptions",), create_subscription),
    ("POST", ("subscriptions", "*", "cancel"), cancel_subscription),
    ("POST", ("usage",), report_usage),
    ("GET", ("invoices", "*"), get_invoice),
    ("GET", ("invoices", "*", "text"), get_invoice_text),
    ("POST", ("invoices", "*", "refund"), refund_invoice),
    ("POST", ("invoices", "*", "void"), void_invoice),
]


def _match(pattern: tuple[str, ...], parts: list[str]) -> list[str] | None:
    if len(pattern) != len(parts):
        return None
    params: list[str] = []
    for expected, actual in zip(pattern, parts):
        if expected == "*":
            params.append(actual)
        elif expected != actual:
            return None
    return params


def handle(engine: BillingEngine, method: str, path: str, body: JSONObject | None = None) -> Response:
    parts = [part for part in path.split("/") if part]
    for route_method, pattern, handler in ROUTES:
        params = _match(pattern, parts)
        if params is None or route_method != method.upper():
            continue
        try:
            return handler(engine, params, body or {})
        except NotFoundError as error:
            return 404, {"error": str(error)}
        except ValidationError as error:
            return 400, {"error": str(error)}
        except BillingError as error:
            return 409, {"error": str(error)}
    return 404, {"error": f"no route for {method} {path}"}


def parse_body(raw: JSONValue) -> JSONObject:
    if not isinstance(raw, dict):
        raise ValidationError("request body must be a JSON object")
    return raw
