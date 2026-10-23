import pytest

from billing import customers, reports
from billing.api import handle
from billing.errors import ValidationError

DAY = 86_400


def test_default_is_seven_days(world):
    cus = world.customer(card=False)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["due_at"] == inv["finalized_at"] + 7 * DAY


def test_custom_terms(world):
    cus = customers.create_customer(world.ctx, "n@example.com", "Net", net_terms_days=30)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["due_at"] == inv["finalized_at"] + 30 * DAY


def test_update_applies_to_next_invoice(world):
    cus = customers.create_customer(world.ctx, "n@example.com", "Net")
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    customers.update_customer(world.ctx, cus["id"], net_terms_days=0)
    world.advance_days(31)
    first, second = world.engine.invoices_for(cus["id"])
    assert first["due_at"] == first["finalized_at"] + 7 * DAY
    assert second["due_at"] == second["finalized_at"]


def test_negative_rejected(world):
    with pytest.raises(ValidationError):
        customers.create_customer(world.ctx, "n@example.com", "Net", net_terms_days=-1)
    cus = customers.create_customer(world.ctx, "m@example.com", "Net")
    with pytest.raises(ValidationError):
        customers.update_customer(world.ctx, cus["id"], net_terms_days=-5)


def test_api(world):
    status, cus = handle(world.engine, "POST", "/customers", {"email": "x@example.com", "name": "X", "net_terms_days": 14})
    assert status == 201
    assert world.ctx.store.customers[cus["id"]]["net_terms_days"] == 14
    status, _ = handle(world.engine, "POST", "/customers", {"email": "y@example.com", "name": "Y", "net_terms_days": -2})
    assert status == 400


def test_aging_follows_terms(world):
    cus = customers.create_customer(world.ctx, "n@example.com", "Net", net_terms_days=30)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)])
    world.clock.advance_days(20)
    assert reports.aging(world.ctx, world.clock.now())["usd"]["current"] == 1000
