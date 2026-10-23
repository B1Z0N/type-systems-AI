from billing import ledger
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
