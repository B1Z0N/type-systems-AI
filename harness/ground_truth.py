"""Answers computed from the L3 source rather than written by hand, so they can't drift from the code.

Tasks refer to these with `{auto: <key>}` in their grader.
- static analysis of the source: which functions can return None, which emit an event
- simulations with the real engine: what dunning does when every charge fails, and the retry
  schedule of a failing webhook
"""

from __future__ import annotations

import ast
import functools
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "subject" / "l3" / "billing"


def _functions(tree: ast.Module, module: str):
    """(qualified name, FunctionDef) for module functions and methods of real (non-typing) classes."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            yield f"{module}.{node.name}", node
        elif isinstance(node, ast.ClassDef) and not any(getattr(b, "id", None) in ("TypedDict", "Protocol") for b in node.bases):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    yield f"{module}.{node.name}.{item.name}", item


def _returns_optional(fn: ast.FunctionDef) -> bool:
    """Annotated `-> X | None` (a function that never returns a value, `-> None`, doesn't count)."""
    members: list[ast.expr] = []

    def flatten(e: ast.expr) -> None:
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.BitOr):
            flatten(e.left)
            flatten(e.right)
        else:
            members.append(e)

    if fn.returns is not None:
        flatten(fn.returns)
    return len(members) > 1 and any(isinstance(m, ast.Constant) and m.value is None for m in members)


SIMULATION = r"""
import json
from billing import catalog, customers, webhooks
from billing.clock import FixedClock, epoch
from billing.engine import BillingEngine
from billing.gateway import FakeGateway, RecordingTransport

# c2: default dunning policy, every charge declined, billing cycle runs daily
clock = FixedClock(epoch(2026, 1, 15, 12))
gateway = FakeGateway()
engine = BillingEngine(clock, gateway, RecordingTransport())
ctx = engine.ctx
price = catalog.create_price(ctx, catalog.create_plan(ctx, "Pro", "pro")["id"], "usd", "per_unit", unit_amount=1000)
cus = customers.create_customer(ctx, "a@example.com", "Ada")
customers.attach_payment_method(ctx, cus["id"], "card", "0002", 12, 2030)  # 0002 always declines
sub = engine.subscribe(cus["id"], [(price["id"], 1)])
[inv] = engine.invoices_for(cus["id"])
attempt_days = [0]
for day in range(1, 40):
    clock.set(inv["finalized_at"] + day * 86400)
    before = len(gateway.charges)
    engine.run_cycle()
    if len(gateway.charges) > before:
        attempt_days.append(day)
truth = {"c2_total_charge_attempts": len(gateway.charges), "c2_last_attempt_day": attempt_days[-1],
         "c2_final_subscription_status": sub["status"], "c2_final_invoice_status": inv["status"]}

# n1: an endpoint that always answers 500, deliveries attempted every second
clock = FixedClock(epoch(2026, 1, 15, 12))
transport = RecordingTransport(status_code=500)
engine = BillingEngine(clock, FakeGateway(), transport)
webhooks.register_endpoint(engine.ctx, "https://h.example.com", ["customer.created"])
customers.create_customer(engine.ctx, "b@example.com", "Bo")
times = []
for second in range(4000):
    sent = len(transport.sent)
    webhooks.deliver_pending(engine.ctx)
    if len(transport.sent) > sent:
        times.append(clock.now())
    clock.advance(1)
truth |= {"n1_delays_seconds": [b - a for a, b in zip(times, times[1:])], "n1_total_attempts": len(times),
          "n1_final_status": engine.ctx.store.deliveries[0]["status"]}
print(json.dumps(truth))
"""


@functools.cache
def compute() -> dict[str, object]:
    optional_returns, updated_emitters = [], []
    for path in sorted(PACKAGE.glob("*.py")):
        for qual, fn in _functions(ast.parse(path.read_text()), path.stem):
            if _returns_optional(fn):
                optional_returns.append(qual)
            if any(isinstance(n, ast.Constant) and n.value == "subscription.updated" for n in ast.walk(fn)):
                updated_emitters.append(qual)
    env = {**os.environ, "PYTHONPATH": str(PACKAGE.parent)}
    simulated = json.loads(subprocess.run([sys.executable, "-c", SIMULATION], capture_output=True, text=True, env=env, check=True).stdout)
    return {"optional_returns": sorted(optional_returns), "subscription_updated_emitters": sorted(updated_emitters), **simulated}


if __name__ == "__main__":
    print(json.dumps(compute(), indent=2))
