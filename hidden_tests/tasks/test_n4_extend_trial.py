import pytest

from billing import events, subscriptions
from billing.errors import InvalidStateError, ValidationError

DAY = 86_400


def test_extends_both_dates_and_emits(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)], trial_days=7)
    trial_end = sub["trial_end"]
    before = len(events.events_of_type(world.ctx, "subscription.updated"))
    subscriptions.extend_trial(world.ctx, sub["id"], 5)
    assert sub["trial_end"] == trial_end + 5 * DAY
    assert sub["current_period_end"] == trial_end + 5 * DAY
    assert len(events.events_of_type(world.ctx, "subscription.updated")) == before + 1


def test_billing_starts_after_extended_trial(world):
    cus = world.customer()
    sub = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)], trial_days=7)
    subscriptions.extend_trial(world.ctx, sub["id"], 3)
    world.advance_days(8)
    assert sub["status"] == "trialing"
    assert world.engine.invoices_for(cus["id"]) == []
    world.advance_days(2)
    assert sub["status"] == "active"
    assert len(world.engine.invoices_for(cus["id"])) == 1


def test_rejects_bad_input(world):
    cus = world.customer()
    trialing = world.engine.subscribe(cus["id"], [(world.monthly["id"], 1)], trial_days=7)
    active = world.engine.subscribe(cus["id"], [(world.team_monthly["id"], 1)])
    for bad in (0, -2):
        with pytest.raises(ValidationError):
            subscriptions.extend_trial(world.ctx, trialing["id"], bad)
    with pytest.raises(InvalidStateError):
        subscriptions.extend_trial(world.ctx, active["id"], 3)
