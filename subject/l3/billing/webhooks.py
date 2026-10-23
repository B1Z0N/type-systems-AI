"""Signed webhook delivery of domain events to external endpoints."""

import hashlib
import hmac
from typing import Final

from .clock import to_millis
from .context import Context
from .errors import ValidationError
from .models import (
    EndpointId,
    EpochSeconds,
    Event,
    EventId,
    EventType,
    WebhookDelivery,
    WebhookEndpoint,
)
from .serialization import dumps

MAX_ATTEMPTS: Final[int] = 5
BASE_BACKOFF_SECONDS: Final[int] = 60
SIGNATURE_TOLERANCE_SECONDS: Final[int] = 300


def register_endpoint(ctx: Context, url: str, event_types: list[EventType] | None = None) -> WebhookEndpoint:
    if not url.startswith("https://"):
        raise ValidationError("webhook endpoints must use https")
    endpoint_id = ctx.ids.endpoint()
    secret = "whsec_" + hashlib.sha256(f"{endpoint_id}:{url}".encode()).hexdigest()[:32]
    endpoint: WebhookEndpoint = {
        "id": endpoint_id,
        "url": url,
        "secret": secret,
        "event_types": event_types,
        "enabled": True,
        "created_at": to_millis(ctx.clock.now()),
    }
    ctx.store.endpoints[endpoint_id] = endpoint
    return endpoint


def disable_endpoint(ctx: Context, endpoint_id: EndpointId) -> None:
    ctx.store.require_endpoint(endpoint_id)["enabled"] = False


def sign(secret: str, timestamp: EpochSeconds, body: bytes) -> str:
    message = str(timestamp).encode() + b"." + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify(secret: str, header: str, body: bytes, now: EpochSeconds) -> bool:
    fields = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    if "t" not in fields or "v1" not in fields or not fields["t"].isdigit():
        return False
    timestamp = EpochSeconds(int(fields["t"]))
    if abs(now - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = sign(secret, timestamp, body)
    return hmac.compare_digest(expected, header)


def wants(endpoint: WebhookEndpoint, event: Event) -> bool:
    if not endpoint["enabled"] or event["created_at"] < endpoint["created_at"]:
        return False
    return endpoint["event_types"] is None or event["type"] in endpoint["event_types"]


def _delivery_for(ctx: Context, endpoint_id: EndpointId, event_id: EventId) -> WebhookDelivery:
    for delivery in ctx.store.deliveries:
        if delivery["endpoint_id"] == endpoint_id and delivery["event_id"] == event_id:
            return delivery
    created: WebhookDelivery = {
        "endpoint_id": endpoint_id,
        "event_id": event_id,
        "status": "pending",
        "attempts": 0,
        "last_status_code": None,
        "next_attempt_at": None,
    }
    ctx.store.deliveries.append(created)
    return created


def _attempt(ctx: Context, endpoint: WebhookEndpoint, event: Event, delivery: WebhookDelivery) -> None:
    now = ctx.clock.now()
    body = dumps({"id": event["id"], "type": event["type"], "created": event["created_at"], "data": event["payload"]})
    headers = {
        "content-type": "application/json",
        "billing-signature": sign(endpoint["secret"], now, body),
        "billing-event-id": event["id"],
    }
    try:
        status = ctx.transport.post(endpoint["url"], body, headers)
    except OSError:
        status = 0
    delivery["attempts"] += 1
    delivery["last_status_code"] = status
    if 200 <= status < 300:
        delivery["status"] = "delivered"
        delivery["next_attempt_at"] = None
    elif delivery["attempts"] >= MAX_ATTEMPTS:
        delivery["status"] = "failed"
        delivery["next_attempt_at"] = None
    else:
        backoff = BASE_BACKOFF_SECONDS * 2 ** (delivery["attempts"] - 1)
        delivery["next_attempt_at"] = EpochSeconds(now + backoff)


def deliver_pending(ctx: Context) -> list[WebhookDelivery]:
    """Send every event each endpoint wants and has not received yet, honouring retry backoff."""
    now = ctx.clock.now()
    touched: list[WebhookDelivery] = []
    for endpoint in ctx.store.endpoints.values():
        for event in ctx.store.events.values():
            if not wants(endpoint, event):
                continue
            delivery = _delivery_for(ctx, endpoint["id"], event["id"])
            if delivery["status"] != "pending":
                continue
            if delivery["next_attempt_at"] is not None and delivery["next_attempt_at"] > now:
                continue
            _attempt(ctx, endpoint, event, delivery)
            touched.append(delivery)
    return touched
