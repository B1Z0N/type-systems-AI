import pytest

from billing import catalog, customers
from billing.clock import FixedClock, epoch
from billing.engine import BillingEngine
from billing.gateway import FakeGateway, RecordingTransport

START = epoch(2026, 1, 15, 12)
DAY = 86_400


class World:
    def __init__(self, policy=None):
        self.clock = FixedClock(START)
        self.gateway = FakeGateway()
        self.transport = RecordingTransport()
        self.engine = BillingEngine(self.clock, self.gateway, self.transport, policy)
        self.ctx = self.engine.ctx
        self.plan = catalog.create_plan(self.ctx, "Pro", "pro")
        self.monthly = catalog.create_price(self.ctx, self.plan["id"], "usd", "per_unit", unit_amount=1000)
        self.team = catalog.create_plan(self.ctx, "Team", "team")
        self.team_monthly = catalog.create_price(self.ctx, self.team["id"], "usd", "per_unit", unit_amount=3000)
        self.api = catalog.create_plan(self.ctx, "API", "api")
        self.api_calls = catalog.create_price(
            self.ctx, self.api["id"], "usd", "metered", unit_amount=2, usage_aggregation="sum"
        )

    def customer(self, last4="4242", region=None, currency="usd", card=True, email="a@example.com"):
        cus = customers.create_customer(self.ctx, email, "Ada", currency, tax_region=region)
        if card:
            customers.attach_payment_method(self.ctx, cus["id"], "card", last4, 12, 2030)
        return cus

    def advance_days(self, days):
        self.clock.advance_days(days)
        return self.engine.run_cycle()


@pytest.fixture
def world():
    return World()
