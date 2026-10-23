import json

from billing import webhooks
from billing.api import handle


def test_webhooks_signed_and_filtered(world):
    endpoint = webhooks.register_endpoint(world.ctx, "https://hooks.example.com", ["invoice.paid"])
    cus = world.customer()
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    world.engine.run_cycle()
    assert len(world.transport.sent) == 1
    url, body, headers = world.transport.sent[0]
    assert json.loads(body)["type"] == "invoice.paid"
    assert webhooks.verify(endpoint["secret"], headers["billing-signature"], body, world.clock.now())
    assert not webhooks.verify(endpoint["secret"], headers["billing-signature"], body + b"x", world.clock.now())


def test_webhook_retry_backoff(world):
    world.transport.status_code = 500
    webhooks.register_endpoint(world.ctx, "https://hooks.example.com", ["customer.created"])
    world.customer()
    webhooks.deliver_pending(world.ctx)
    webhooks.deliver_pending(world.ctx)
    assert len(world.transport.sent) == 1
    world.clock.advance(60)
    webhooks.deliver_pending(world.ctx)
    assert len(world.transport.sent) == 2
    delivery = world.ctx.store.deliveries[0]
    assert delivery["next_attempt_at"] == world.clock.now() + 120


def test_api_round_trip(world):
    status, cus = handle(world.engine, "POST", "/customers", {"email": "x@example.com", "name": "X"})
    assert status == 201
    status, sub = handle(
        world.engine,
        "POST",
        "/subscriptions",
        {"customer": cus["id"], "items": [{"price": world.api_calls["id"], "quantity": 0}]},
    )
    assert status == 201
    status, rec = handle(
        world.engine,
        "POST",
        "/usage",
        {
            "subscription": sub["id"],
            "price": world.api_calls["id"],
            "quantity": 3,
            "timestamp": (world.clock.now() + 5) * 1000,
        },
    )
    assert status == 201 and rec["timestamp"] == (world.clock.now() + 5) * 1000
    status, err = handle(world.engine, "GET", "/invoices/in_999999")
    assert status == 404
    assert handle(world.engine, "POST", "/usage", {"subscription": sub["id"]})[0] == 400
