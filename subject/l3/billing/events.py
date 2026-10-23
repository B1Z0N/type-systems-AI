"""Domain events emitted by billing operations."""

from .clock import to_millis
from .context import Context
from .models import Event, EventListener, EventType, JSONObject


def emit(ctx: Context, event_type: EventType, payload: JSONObject) -> Event:
    event: Event = {
        "id": ctx.ids.event(),
        "type": event_type,
        "created_at": to_millis(ctx.clock.now()),
        "payload": payload,
    }
    ctx.store.events[event["id"]] = event
    for listener in list(ctx.listeners):
        listener(event)
    return event


def subscribe(ctx: Context, listener: EventListener) -> None:
    ctx.listeners.append(listener)


def unsubscribe(ctx: Context, listener: EventListener) -> None:
    if listener in ctx.listeners:
        ctx.listeners.remove(listener)


def events_of_type(ctx: Context, event_type: EventType) -> list[Event]:
    return [event for event in ctx.store.events.values() if event["type"] == event_type]
