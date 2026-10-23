from billing import discounts, tax


def test_percent_coupon_once(world):
    cus = world.customer()
    coupon = discounts.create_coupon(world.ctx, "20OFF", percent_off=2000)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)], coupon_id=coupon["id"])
    world.advance_days(31)
    first, second = world.engine.invoices_for(cus["id"])
    assert first["discount_total"] == 200
    assert first["total"] == 800
    assert second["discount_total"] == 0


def test_repeating_coupon(world):
    cus = world.customer()
    coupon = discounts.create_coupon(world.ctx, "3x", percent_off=1000, duration="repeating", duration_in_periods=2)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)], coupon_id=coupon["id"])
    world.advance_days(31)
    world.advance_days(31)
    totals = [inv["total"] for inv in world.engine.invoices_for(cus["id"])]
    assert totals == [900, 900, 1000]


def test_amount_off_allocated_across_lines(world):
    cus = world.customer()
    coupon = discounts.create_coupon(world.ctx, "5USD", amount_off=500, currency="usd")
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1), (world.team_monthly["id"], 1)], coupon_id=coupon["id"])
    [inv] = world.engine.invoices_for(cus["id"])
    assert [line["discount_amount"] for line in inv["lines"]] == [125, 375]
    assert inv["total"] == 3500


def test_amount_off_wrong_currency_ignored(world):
    cus = world.customer()
    coupon = discounts.create_coupon(world.ctx, "5EUR", amount_off=500, currency="eur")
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)], coupon_id=coupon["id"])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["discount_total"] == 0


def test_plan_restricted_coupon(world):
    cus = world.customer()
    coupon = discounts.create_coupon(world.ctx, "team", percent_off=5000, applies_to_plans=[world.team["id"]])
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1), (world.team_monthly["id"], 1)], coupon_id=coupon["id"])
    [inv] = world.engine.invoices_for(cus["id"])
    assert [line["discount_amount"] for line in inv["lines"]] == [0, 1500]


def test_exclusive_tax_after_discount(world):
    tax.set_tax_rate(world.ctx, "US-CA", 825, "CA sales tax")
    cus = world.customer(region="us-ca")
    coupon = discounts.create_coupon(world.ctx, "10", percent_off=1000)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)], coupon_id=coupon["id"])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["tax_total"] == 74
    assert inv["total"] == 900 + 74


def test_inclusive_tax(world):
    from billing import catalog

    price = catalog.create_price(world.ctx, world.plan["id"], "usd", "flat", unit_amount=1200, tax_inclusive=True)
    tax.set_tax_rate(world.ctx, "DE", 2000, "VAT")
    cus = world.customer(region="DE")
    world.engine.subscribe(cus["id"], [(price["id"], 1)])
    [inv] = world.engine.invoices_for(cus["id"])
    assert inv["tax_total"] == 200
    assert inv["total"] == 1200


def test_region_fallback_and_exempt(world):
    tax.set_tax_rate(world.ctx, "US", 500, "federal-ish")
    cus = world.customer(region="US-NY")
    assert tax.rate_for_customer(world.ctx, cus) == 500
    cus["tax_exempt"] = True
    assert tax.rate_for_customer(world.ctx, cus) == 0
    nobody = world.customer(region=None, email="b@example.com")
    assert tax.rate_for_customer(world.ctx, nobody) == 0


def test_exhausted_coupon_rejected_before_subscription_exists(world):
    import pytest

    from billing.errors import InvalidStateError

    cus = world.customer()
    coupon = discounts.create_coupon(world.ctx, "one", percent_off=1000, max_redemptions=1)
    world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)], coupon_id=coupon["id"])
    before = len(world.ctx.store.subscriptions)
    with pytest.raises(InvalidStateError):
        world.engine.subscribe(cus["id"], [(world.team_monthly["id"], 1)], coupon_id=coupon["id"])
    assert len(world.ctx.store.subscriptions) == before
