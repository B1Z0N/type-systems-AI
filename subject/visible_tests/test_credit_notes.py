import pytest

from billing import ledger, refunds


def test_credit_note_limits(world):
    cus = world.customer()
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    refunds.credit_invoice(world.ctx, inv["id"], 400, "goodwill")
    assert ledger.balance(world.ctx, cus["id"]) == 400
    with pytest.raises(Exception):
        refunds.credit_invoice(world.ctx, inv["id"], 700, "too much")
