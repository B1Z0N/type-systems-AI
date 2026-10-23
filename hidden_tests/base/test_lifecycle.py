from billing import invoices, ledger, subscriptions
from billing.clock import format_date


def test_subscribe_invoices_and_charges_first_period(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 2)])
    assert sub["status"] == "active"
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["status"] == "paid"
    assert inv["total"] == 2000
    assert inv["number"] == "INV-00001"
    assert format_date(sub["current_period_end"]) == "2026-02-15"


def test_renewal_bills_usage_in_arrears(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1), (world.api_calls["id"], 0)])
    world.engine.report_usage(sub["id"], world.api_calls["id"], 300)
    world.clock.advance_days(3)
    world.engine.report_usage(sub["id"], world.api_calls["id"], 200)
    created = world.advance_days(28)
    assert len(created) == 1
    usage_line = [line for line in created[0]["lines"] if line["kind"] == "usage"][0]
    assert usage_line["quantity"] == 500
    assert usage_line["amount"] == 1000
    assert created[0]["total"] == 2000


def test_usage_outside_period_not_billed(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.api_calls["id"], 0)])
    start_ms = sub["current_period_start"] * 1000
    world.engine.report_usage(sub["id"], world.api_calls["id"], 7, timestamp=start_ms - 1)
    world.engine.report_usage(sub["id"], world.api_calls["id"], 5, timestamp=start_ms)
    created = world.advance_days(31)
    assert created[0]["lines"][0]["quantity"] == 5


def test_trial_converts_and_invoices(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)], trial_days=14)
    assert sub["status"] == "trialing"
    assert world.engine.invoices_for(cus["id"]) == []
    world.advance_days(14)
    assert sub["status"] == "active"
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["total"] == 1000


def test_cancel_at_period_end(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    world.engine.cancel(sub["id"])
    assert sub["status"] == "active"
    world.advance_days(31)
    assert sub["status"] == "canceled"
    assert len(world.engine.invoices_for(cus["id"])) == 1


def test_upgrade_prorates_on_next_invoice(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    period = sub["current_period_end"] - sub["current_period_start"]
    world.clock.advance(period // 2)
    world.engine.change_plan(sub["id"], [(world.team_monthly["id"], 1)])
    created = world.advance_days(16)
    lines = created[0]["lines"]
    prorations = [line for line in lines if line["kind"] == "proration"]
    assert [line["amount"] for line in prorations] == [-500, 1500]
    assert created[0]["total"] == 3000 + 1000


def test_credit_applied_on_finalize(world):
    cus = world.customer()
    ledger.grant_credit(world.ctx, cus["id"], 700)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["credit_applied"] == 700
    assert inv["amount_paid"] == 300
    assert ledger.balance(world.ctx, cus["id"]) == 0


def test_void_restores_credit(world):
    cus = world.customer(card=False)
    ledger.grant_credit(world.ctx, cus["id"], 400)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["status"] == "open"
    invoices.void(world.ctx, inv["id"])
    assert ledger.balance(world.ctx, cus["id"]) == 400


def test_pause_and_resume(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    subscriptions.pause(world.ctx, sub["id"])
    assert world.advance_days(40) == []
    subscriptions.resume(world.ctx, sub["id"])
    assert sub["status"] == "active"
    assert sub["current_period_start"] == world.clock.now()
