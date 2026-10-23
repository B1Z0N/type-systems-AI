"""Signed webhook delivery of domain events to external endpoints.

Signatures follow the "t=<epoch seconds>,v1=<hex HMAC-SHA256>" header scheme, where the
HMAC covers "<timestamp>.<body>". Note that event and endpoint `created_at` values are
epoch milliseconds, while signature timestamps and retry times are epoch seconds.
"""

import hashlib
import hmac

from .clock import to_millis
from .errors import ValidationError
from .serialization import dumps

MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 60
SIGNATURE_TOLERANCE_SECONDS = 300


def register_endpoint(ctx, url, event_types=None):
    """Register an https endpoint receiving `event_types` (None means every type).

    The signing secret is derived deterministically from the endpoint id and url.
    Only events created after registration are delivered. Raises ValidationError for
    non-https urls.
    """
    if not url.startswith("https://"):
        raise ValidationError("webhook endpoints must use https")
    endpoint_id = ctx.ids.endpoint()
    secret = "whsec_" + hashlib.sha256(f"{endpoint_id}:{url}".encode()).hexdigest()[:32]
    endpoint = {
        "id": endpoint_id,
        "url": url,
        "secret": secret,
        "event_types": event_types,
        "enabled": True,
        "created_at": to_millis(ctx.clock.now()),
    }
    ctx.store.endpoints[endpoint_id] = endpoint
    return endpoint


def disable_endpoint(ctx, endpoint_id):
    """Stop delivering to the endpoint; its delivery history is kept."""
    ctx.store.require_endpoint(endpoint_id)["enabled"] = False


def sign(secret, timestamp, body):
    """Signature header value for `body` (bytes) sent at epoch second `timestamp`."""
    signed_message = str(timestamp).encode() + b"." + body
    digest = hmac.new(secret.encode(), signed_message, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify(secret, header, body, now):
    """Whether `header` is a valid signature of `body` that is not too old or too far ahead.

    `now` is epoch seconds; timestamps more than SIGNATURE_TOLERANCE_SECONDS away are
    rejected to limit replay attacks. Malformed headers return False instead of raising.
    """
    header_fields = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    if "t" not in header_fields or "v1" not in header_fields or not header_fields["t"].isdigit():
        return False
    timestamp = int(header_fields["t"])
    if abs(now - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected_header = sign(secret, timestamp, body)
    # Constant-time comparison so the signature can't be guessed byte by byte.
    return hmac.compare_digest(expected_header, header)


def wants(endpoint, event):
    """Whether the endpoint is enabled, existed when the event happened and subscribes to its type."""
    if not endpoint["enabled"] or event["created_at"] < endpoint["created_at"]:
        return False
    return endpoint["event_types"] is None or event["type"] in endpoint["event_types"]


def _delivery_for(ctx, endpoint_id, event_id):
    """The delivery record for this endpoint and event, creating a pending one if needed."""
    for delivery in ctx.store.deliveries:
        if delivery["endpoint_id"] == endpoint_id and delivery["event_id"] == event_id:
            return delivery
    new_delivery = {
        "endpoint_id": endpoint_id,
        "event_id": event_id,
        "status": "pending",
        "attempts": 0,
        "last_status_code": None,
        "next_attempt_at": None,
    }
    ctx.store.deliveries.append(new_delivery)
    return new_delivery


def _attempt(ctx, endpoint, event, delivery):
    """POST the event once and update the delivery's status and retry schedule.

    Any 2xx response counts as delivered. Network errors are recorded as status 0.
    Failed attempts back off exponentially (60s, 120s, 240s, ...) until MAX_ATTEMPTS.
    """
    now = ctx.clock.now()
    body = dumps({"id": event["id"], "type": event["type"], "created": event["created_at"], "data": event["payload"]})
    headers = {
        "content-type": "application/json",
        "billing-signature": sign(endpoint["secret"], now, body),
        "billing-event-id": event["id"],
    }
    try:
        status_code = ctx.transport.post(endpoint["url"], body, headers)
    except OSError:
        status_code = 0
    delivery["attempts"] += 1
    delivery["last_status_code"] = status_code
    if 200 <= status_code < 300:
        delivery["status"] = "delivered"
        delivery["next_attempt_at"] = None
    elif delivery["attempts"] >= MAX_ATTEMPTS:
        delivery["status"] = "failed"
        delivery["next_attempt_at"] = None
    else:
        backoff_seconds = BASE_BACKOFF_SECONDS * 2 ** (delivery["attempts"] - 1)
        delivery["next_attempt_at"] = now + backoff_seconds


def deliver_pending(ctx):
    """Send every event each endpoint wants and has not received yet, honouring retry backoff.

    Returns the delivery records that were attempted during this call.
    """
    now = ctx.clock.now()
    attempted_deliveries = []
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
            attempted_deliveries.append(delivery)
    return attempted_deliveries
