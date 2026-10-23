import pytest

from billing import ledger, refunds
from billing.errors import InvalidStateError


def test_failed_payment_goes_past_due_then_unpaid(world):
    cus = world.customer(last4="0002")
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["status"] == "open"
    assert sub["status"] == "past_due"
    assert inv["attempt_count"] == 1
    for _ in range(7):
        world.advance_days(1)
    assert inv["attempt_count"] == 4
    assert sub["status"] == "unpaid"
    assert inv["status"] == "open"
    assert inv["next_attempt_at"] is None
    assert len(world.gateway.charges) == 4


def test_cancel_policy_marks_uncollectible():
    from conftest import World

    w = World({"retry_offsets_days": [2], "final_action": "cancel"})
    cus = w.customer(last4="9995")
    sub = w.engine.subscribe(cus["id"], [(w.monthly["id"], 1)])
    w.advance_days(2)
    [inv] = w.engine.invoices_for(cus["id"])
    assert inv["status"] == "uncollectible"
    assert sub["status"] == "canceled"


def test_recovery_after_retry_succeeds(world):
    cus = world.customer()
    world.gateway.fail_next.append("card_declined")
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    assert sub["status"] == "past_due"
    world.advance_days(1)
    assert sub["status"] == "active"
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["status"] == "paid"


def test_customer_without_card_enters_dunning(world):
    cus = world.customer(card=False)
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["attempt_count"] == 1
    assert inv["next_attempt_at"] is not None
    assert sub["status"] == "past_due"


def test_full_refund(world):
    cus = world.customer()
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    note = world.engine.refund(inv["id"])
    assert note["amount"] == 1000
    assert world.gateway.refunds == [("ch_000001", 1000)]
    [payment] = world.ctx.store.payments_for_invoice(inv["id"])
    assert payment["status"] == "refunded"
    with pytest.raises(InvalidStateError):
        world.engine.refund(inv["id"])


def test_refund_returns_applied_credit(world):
    cus = world.customer()
    ledger.grant_credit(world.ctx, cus["id"], 300)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    note = world.engine.refund(inv["id"])
    assert note["amount"] == 1000
    assert ledger.balance(world.ctx, cus["id"]) == 300


def test_refund_with_multiple_customers(world):
    first = world.customer()
    second = world.customer(email="b@example.com")
    world.engine.subscribe(first["id"], [(world.monthly["id"], 1)])
    world.engine.subscribe(second["id"], [(world.team_monthly["id"], 1)])
    [inv] = world.engine.invoices_for(second["id"])
    assert world.engine.refund(inv["id"])["amount"] == 3000


def test_credit_note_limits(world):
    cus = world.customer()
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    refunds.credit_invoice(world.ctx, inv["id"], 400, "goodwill")
    assert ledger.balance(world.ctx, cus["id"]) == 400
    with pytest.raises(Exception):
        refunds.credit_invoice(world.ctx, inv["id"], 700, "too much")


def test_retries_exhausted_after_cancel_marks_uncollectible():
    from conftest import World

    w = World({"retry_offsets_days": [1], "final_action": "mark_unpaid"})
    cus = w.customer(last4="0002")
    sub = w.engine.subscribe(cus["id"], [(w.monthly["id"], 1)])
    w.engine.cancel(sub["id"], at_period_end=False)
    w.advance_days(1)
    [inv] = w.engine.invoices_for(cus["id"])
    assert sub["status"] == "canceled"
    assert inv["status"] == "uncollectible"
