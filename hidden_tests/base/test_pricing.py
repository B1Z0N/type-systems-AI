import pytest

from billing import catalog
from billing.errors import ValidationError
from billing.pricing import amount_for_quantity

TIERS = [
    {"up_to": 10, "unit_amount": 100, "flat_amount": 0},
    {"up_to": 20, "unit_amount": 80, "flat_amount": 500},
    {"up_to": None, "unit_amount": 50, "flat_amount": 0},
]


def test_graduated(world):
    price = catalog.create_price(world.ctx, world.plan["id"], "usd", "tiered_graduated", tiers=TIERS)
    assert amount_for_quantity(price, 5) == 500
    assert amount_for_quantity(price, 15) == 1000 + 400 + 500
    assert amount_for_quantity(price, 25) == 1000 + 800 + 500 + 250


def test_volume(world):
    price = catalog.create_price(world.ctx, world.plan["id"], "usd", "tiered_volume", tiers=TIERS)
    assert amount_for_quantity(price, 5) == 500
    assert amount_for_quantity(price, 15) == 15 * 80 + 500
    assert amount_for_quantity(price, 0) == 0


def test_flat_ignores_quantity(world):
    price = catalog.create_price(world.ctx, world.plan["id"], "usd", "flat", unit_amount=4900)
    assert amount_for_quantity(price, 3) == 4900


def test_tier_validation(world):
    with pytest.raises(ValidationError):
        catalog.create_price(world.ctx, world.plan["id"], "usd", "tiered_volume", tiers=TIERS[:2])
    with pytest.raises(ValidationError):
        catalog.create_price(world.ctx, world.plan["id"], "usd", "metered", unit_amount=1)
