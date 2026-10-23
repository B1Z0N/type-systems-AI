import pytest

from billing import ledger
from billing.api import handle
from billing.errors import InvalidStateError, ValidationError


def paid_invoice(world, quantity=1, credit=0):
    cus = world.customer()
    if credit:
        ledger.grant_credit(world.ctx, cus["id"], credit)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], quantity)])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["status"] == "paid"
    return cus, inv


def test_partial_then_rest(world):
    cus, inv = paid_invoice(world)
    note = world.engine.refund(inv["id"], amount=300)
    assert note["amount"] == 300 and note["refunded"] is True
    [payment] = world.ctx.store.payments_for_invoice(inv["id"])
    assert payment["status"] == "partially_refunded"
    assert payment["refunded_amount"] == 300
    rest = world.engine.refund(inv["id"])
    assert rest["amount"] == 700
    assert payment["status"] == "refunded"
    assert world.gateway.refunds == [("ch_000001", 300), ("ch_000001", 700)]
    with pytest.raises(InvalidStateError):
        world.engine.refund(inv["id"])


def test_multiple_partials_until_exhausted(world):
    cus, inv = paid_invoice(world)
    world.engine.refund(inv["id"], amount=400)
    world.engine.refund(inv["id"], amount=600)
    [payment] = world.ctx.store.payments_for_invoice(inv["id"])
    assert payment["status"] == "refunded"
    assert len(world.ctx.store.credit_notes_for(inv["id"])) == 2
    with pytest.raises((InvalidStateError, ValidationError)):
        world.engine.refund(inv["id"], amount=1)


def test_invalid_amounts(world):
    cus, inv = paid_invoice(world)
    with pytest.raises(ValidationError):
        world.engine.refund(inv["id"], amount=0)
    with pytest.raises(ValidationError):
        world.engine.refund(inv["id"], amount=1001)
    assert world.gateway.refunds == []


def test_partial_does_not_return_credit(world):
    cus, inv = paid_invoice(world, credit=300)
    world.engine.refund(inv["id"], amount=200)
    assert ledger.balance(world.ctx, cus["id"]) == 0
    note = world.engine.refund(inv["id"])
    assert note["amount"] == 500 + 300
    assert ledger.balance(world.ctx, cus["id"]) == 300


def test_oldest_payment_first(world):
    cus = world.customer(card=False)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    from billing import customers

    customers.attach_payment_method(world.ctx, cus["id"], "card", "4242", 12, 2030)
    inv["amount_due"] = 400
    world.engine.pay(inv["id"])
    inv["status"] = "open"
    inv["amount_due"] = 600
    inv["attempt_count"] += 1
    world.clock.advance(10)
    world.engine.pay(inv["id"])
    assert inv["status"] == "paid"
    first, second = sorted(world.ctx.store.payments_for_invoice(inv["id"]), key=lambda p: p["created_at"])
    world.engine.refund(inv["id"], amount=500)
    assert first["refunded_amount"] == 400 and first["status"] == "refunded"
    assert second["refunded_amount"] == 100 and second["status"] == "partially_refunded"


def test_api_amount(world):
    cus, inv = paid_invoice(world)
    status, body = handle(world.engine, "POST", f"/invoices/{inv['id']}/refund", {"amount": 250})
    assert status == 200 and body["amount"] == 250
    status, body = handle(world.engine, "POST", f"/invoices/{inv['id']}/refund", {"amount": 5000})
    assert status == 400
