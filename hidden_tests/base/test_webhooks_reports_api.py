import json

from billing import reports, webhooks
from billing.api import handle
from billing.render import render_invoice


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


def test_endpoint_only_gets_new_events(world):
    world.customer()
    world.clock.advance(1)
    webhooks.register_endpoint(world.ctx, "https://hooks.example.com")
    webhooks.deliver_pending(world.ctx)
    assert world.transport.sent == []


def test_mrr_and_aging(world):
    from billing import catalog

    yearly = catalog.create_price(world.ctx, world.plan["id"], "usd", "flat", unit_amount=12000, interval="year")
    a = world.customer()
    b = world.customer(email="b@example.com", card=False)
    world.engine.subscribe(a["id"], [(world.monthly["id"], 3)])
    world.engine.subscribe(b["id"], [(yearly["id"], 1)])
    assert reports.mrr(world.ctx) == {"usd": 3000 + 1000}
    world.clock.advance_days(40)
    aging = reports.aging(world.ctx, world.clock.now())
    assert aging["usd"]["31-60"] == 12000


def test_revenue_between_nets_credit_notes(world):
    cus = world.customer()
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    world.engine.refund(inv["id"])
    assert reports.revenue_between(world.ctx, inv["paid_at"], inv["paid_at"] + 1) == {"usd": 0}


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


def test_render_invoice(world):
    from billing import tax

    tax.set_tax_rate(world.ctx, "US-CA", 825, "CA")
    cus = world.customer(region="US-CA")
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    text = render_invoice(world.ctx, inv)
    assert "Invoice INV-00001" in text
    assert "Tax (8.25%)" in text
    assert "$10.82" in text
