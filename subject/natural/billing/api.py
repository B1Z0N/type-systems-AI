"""JSON request handling for the public billing API.

Handlers take (engine, path params, body dict) and return (http status, JSON payload).
`handle` routes a request and maps billing errors to 404, 400 or 409 responses.
"""

from . import customers, invoices
from .errors import BillingError, NotFoundError, ValidationError
from .render import render_invoice
from .serialization import customer_json, invoice_json, subscription_json


def _required_str(body, key):
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{key} must be a non-empty string")
    return value


def _optional_str(body, key):
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be a string")
    return value


def _int(body, key, default=None):
    """`body[key]` as an int (or `default` when absent); an explicit null is rejected."""
    value = body.get(key, default)
    # bool is a subclass of int, but true/false is never a valid count.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{key} must be an integer")
    return value


def _items(body):
    """Parse [{"price": id, "quantity": n}, ...] into (price_id, quantity) pairs; quantity defaults to 1."""
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError("items must be a non-empty list")
    items = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValidationError("each item must be an object")
        items.append((_required_str(raw_item, "price"), _int(raw_item, "quantity", 1)))
    return items


def create_customer(engine, _, body):
    customer = customers.create_customer(
        engine.ctx,
        _required_str(body, "email"),
        _required_str(body, "name"),
        _optional_str(body, "currency") or "usd",
        tax_region=_optional_str(body, "tax_region"),
    )
    return 201, customer_json(customer)


def create_subscription(engine, _, body):
    raw_trial_days = body.get("trial_days")
    subscription = engine.subscribe(
        _required_str(body, "customer"),
        _items(body),
        trial_days=_int(body, "trial_days") if raw_trial_days is not None else None,
    )
    return 201, subscription_json(subscription)


def report_usage(engine, _, body):
    """Usage timestamps arrive as epoch milliseconds, matching the client SDKs."""
    raw_timestamp = body.get("timestamp")
    timestamp_ms = _int(body, "timestamp") if raw_timestamp is not None else None
    record = engine.report_usage(
        _required_str(body, "subscription"),
        _required_str(body, "price"),
        _int(body, "quantity"),
        timestamp_ms,
        _optional_str(body, "idempotency_key"),
    )
    return 201, {"id": record["id"], "quantity": record["quantity"], "timestamp": record["timestamp"]}


def get_invoice(engine, params, _):
    invoice = engine.ctx.store.require_invoice(params[0])
    return 200, invoice_json(invoice)


def get_invoice_text(engine, params, _):
    invoice = engine.ctx.store.require_invoice(params[0])
    return 200, {"text": render_invoice(engine.ctx, invoice)}


def refund_invoice(engine, params, body):
    note = engine.refund(params[0], _optional_str(body, "reason") or "requested_by_customer")
    return 200, {"credit_note": note["id"], "amount": note["amount"]}


def void_invoice(engine, params, _):
    invoice = invoices.void(engine.ctx, params[0])
    return 200, invoice_json(invoice)


def cancel_subscription(engine, params, body):
    at_period_end = body.get("at_period_end", True)
    if not isinstance(at_period_end, bool):
        raise ValidationError("at_period_end must be a boolean")
    subscription = engine.cancel(params[0], at_period_end=at_period_end)
    return 200, subscription_json(subscription)


# (HTTP method, path segments, handler); "*" matches one segment and is passed as a param.
ROUTES = [
    ("POST", ("customers",), create_customer),
    ("POST", ("subscriptions",), create_subscription),
    ("POST", ("subscriptions", "*", "cancel"), cancel_subscription),
    ("POST", ("usage",), report_usage),
    ("GET", ("invoices", "*"), get_invoice),
    ("GET", ("invoices", "*", "text"), get_invoice_text),
    ("POST", ("invoices", "*", "refund"), refund_invoice),
    ("POST", ("invoices", "*", "void"), void_invoice),
]


def _match(pattern, path_segments):
    """Wildcard values if the segments match `pattern`, otherwise None."""
    if len(pattern) != len(path_segments):
        return None
    params = []
    for expected, actual in zip(pattern, path_segments):
        if expected == "*":
            params.append(actual)
        elif expected != actual:
            return None
    return params


def handle(engine, method, path, body=None):
    """Dispatch a request to its route and return (status, payload).

    Empty path segments are ignored and the method is case-insensitive. Unknown routes
    (including a known path with the wrong method) return 404.
    """
    path_segments = [segment for segment in path.split("/") if segment]
    for route_method, pattern, handler in ROUTES:
        params = _match(pattern, path_segments)
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


def parse_body(raw):
    """Return `raw` if it is a JSON object (dict); raises ValidationError otherwise."""
    if not isinstance(raw, dict):
        raise ValidationError("request body must be a JSON object")
    return raw
