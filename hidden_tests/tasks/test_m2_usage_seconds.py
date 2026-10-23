from billing.api import handle


def test_record_stored_in_seconds(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.api_calls["id"], 0)])
    now = world.clock.now()
    explicit = world.engine.report_usage(sub["id"], world.api_calls["id"], 3, timestamp=(now + 10) * 1000 + 999)
    assert explicit["timestamp"] == now + 10
    default = world.engine.report_usage(sub["id"], world.api_calls["id"], 4)
    assert default["timestamp"] == now


def test_api_keeps_milliseconds(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.api_calls["id"], 0)])
    ms = (world.clock.now() + 30) * 1000
    status, body = handle(
        world.engine,
        "POST",
        "/usage",
        {"subscription": sub["id"], "price": world.api_calls["id"], "quantity": 2, "timestamp": ms},
    )
    assert status == 201
    assert body["timestamp"] == ms
    stored = world.ctx.store.usage_records[body["id"]]
    assert stored["timestamp"] == ms // 1000


def test_billing_unchanged(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.api_calls["id"], 0)])
    start_ms = sub["current_period_start"] * 1000
    world.engine.report_usage(sub["id"], world.api_calls["id"], 7, timestamp=start_ms - 1)
    world.engine.report_usage(sub["id"], world.api_calls["id"], 5, timestamp=start_ms)
    world.engine.report_usage(sub["id"], world.api_calls["id"], 6)
    end_ms = sub["current_period_end"] * 1000
    world.engine.report_usage(sub["id"], world.api_calls["id"], 100, timestamp=end_ms)
    created = world.advance_days(31)
    assert created[0]["lines"][0]["quantity"] == 11


def test_last_aggregation_uses_latest(world):
    from billing import catalog

    gauge = catalog.create_price(
        world.ctx, world.api["id"], "usd", "metered", unit_amount=1, usage_aggregation="last"
    )
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(gauge["id"], 0)])
    base = sub["current_period_start"] * 1000
    world.engine.report_usage(sub["id"], gauge["id"], 9, timestamp=base + 5000)
    world.engine.report_usage(sub["id"], gauge["id"], 4, timestamp=base + 2000)
    created = world.advance_days(31)
    assert created[0]["lines"][0]["quantity"] == 9
