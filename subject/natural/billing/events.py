"""Domain events emitted by billing operations."""

from .clock import to_millis


def emit(ctx, event_type, payload):
    """Record an event in the store and notify every registered listener.

    The event's `created_at` is in epoch milliseconds. Listeners are called
    synchronously, in registration order, and may (un)subscribe while being notified.
    Returns the stored event dict.
    """
    event = {"id": ctx.ids.event(), "type": event_type, "created_at": to_millis(ctx.clock.now()), "payload": payload}
    ctx.store.events[event["id"]] = event
    # Iterate over a copy so listeners can unsubscribe themselves.
    for listener in list(ctx.listeners):
        listener(event)
    return event


def subscribe(ctx, listener):
    """Register a callable that receives every subsequently emitted event."""
    ctx.listeners.append(listener)


def unsubscribe(ctx, listener):
    """Remove a listener; does nothing if it was never registered."""
    if listener in ctx.listeners:
        ctx.listeners.remove(listener)


def events_of_type(ctx, event_type):
    """All stored events of the given type, oldest first."""
    return [event for event in ctx.store.events.values() if event["type"] == event_type]
