"""Differential testing: replay random operation sequences against two billing packages.

Every operation's result (or exception type) and the final state are recorded as JSON,
so two packages that should behave the same must produce byte-identical traces. Used to

- check that every arm (and l0-natural) is behaviorally identical to L3, beyond the tests
- check an agent's modification against the reference solution ("passes tests, still wrong")

  uv run python tools/difftest.py build/arms/l3 build/arms/l0 --seeds 200
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DRIVER = r'''
import json, random, sys, traceback
from billing import catalog, customers, discounts, invoices, ledger, refunds, reports, subscriptions, tax, webhooks
from billing.api import handle
from billing.clock import FixedClock, epoch
from billing.engine import BillingEngine
from billing.gateway import FakeGateway, RecordingTransport

def summarize(value, depth=0):
    if isinstance(value, dict):
        return {str(k): summarize(v, depth + 1) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [summarize(v, depth + 1) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(type(value).__name__)

def run(seed, steps):
    rng = random.Random(seed)
    clock = FixedClock(epoch(2026, 1, 1 + rng.randrange(28), rng.randrange(24)))
    gateway, transport = FakeGateway(), RecordingTransport()
    policy = rng.choice([None, {"retry_offsets_days": [2, 5], "final_action": rng.choice(["cancel", "pause", "mark_unpaid"])}])
    engine = BillingEngine(clock, gateway, transport, policy)
    ctx = engine.ctx
    ctx.fx.set_rate("eur", "usd", "1.1")
    for region, rate in (("US-CA", 825), ("US", 500), ("DE", 1900)):
        tax.set_tax_rate(ctx, region, rate, region)
    plans = [catalog.create_plan(ctx, name, name.lower(), rng.choice([0, 0, 7])) for name in ("Basic", "Pro", "Usage")]
    tiers = [{"up_to": 5, "unit_amount": 300, "flat_amount": 0}, {"up_to": None, "unit_amount": 150, "flat_amount": 200}]
    prices = []
    for currency in ("usd", "eur"):
        for interval in ("month", "year"):
            prices.append(catalog.create_price(ctx, plans[0]["id"], currency, "per_unit", unit_amount=rng.choice([999, 1500]), interval=interval))
            prices.append(catalog.create_price(ctx, plans[1]["id"], currency, "flat", unit_amount=4900, interval=interval,
                                               tax_inclusive=rng.random() < 0.3))
            prices.append(catalog.create_price(ctx, plans[1]["id"], currency, rng.choice(["tiered_graduated", "tiered_volume"]), tiers=tiers, interval=interval))
            prices.append(catalog.create_price(ctx, plans[2]["id"], currency, "metered", unit_amount=rng.choice([1, 3]),
                                               usage_aggregation=rng.choice(["sum", "max", "last"]), interval=interval))
    custs, subs, coupons, out = [], [], [], []

    def pick(seq):
        return rng.choice(seq) if seq else None

    def op_customer():
        currency = "usd" if rng.random() < 0.8 else "eur"
        cus = customers.create_customer(ctx, f"u{len(custs)}@example.com", f"User {len(custs)}", currency,
                                        tax_region=rng.choice([None, "US-CA", "us-ny", "DE", "FR"]), tax_exempt=rng.random() < 0.1)
        if rng.random() < 0.85:
            customers.attach_payment_method(ctx, cus["id"], "card", rng.choice(["4242", "4242", "4242", "0002", "9995"]), 12, rng.choice([2025, 2030]))
        custs.append(cus)
        return cus

    def op_coupon():
        if rng.random() < 0.5:
            c = discounts.create_coupon(ctx, "p", percent_off=rng.choice([1000, 2500, 10000]), duration=rng.choice(["once", "forever"]),
                                        applies_to_plans=rng.choice([None, [plans[0]["id"]]]))
        else:
            c = discounts.create_coupon(ctx, "a", amount_off=rng.choice([300, 5000]), currency=rng.choice(["usd", "eur"]),
                                        duration="repeating", duration_in_periods=rng.choice([1, 3]), max_redemptions=rng.choice([None, 2]))
        coupons.append(c)
        return c

    def items_for(cus):
        interval = rng.choice(["month", "year"])
        pool = [p for p in prices if p["currency"] == cus["currency"] and p["interval"] == interval]
        chosen = rng.sample(pool, rng.choice([1, 1, 2]))
        return [(p["id"], 0 if p["model"] == "metered" else rng.choice([1, 2, 7])) for p in chosen]

    def op_subscribe():
        cus = pick(custs) or op_customer()
        coupon = pick(coupons) if rng.random() < 0.3 else None
        sub = engine.subscribe(cus["id"], items_for(cus), trial_days=rng.choice([None, 0, 14]), coupon_id=coupon["id"] if coupon else None)
        subs.append(sub)
        return sub

    def op_usage():
        sub = pick(subs)
        metered = [i for i in sub["items"] if ctx.store.prices[i["price_id"]]["model"] == "metered"] if sub else []
        if not metered:
            return "skip"
        ts = None if rng.random() < 0.5 else (clock.now() + rng.randrange(-40, 40) * 86400) * 1000
        return engine.report_usage(sub["id"], metered[0]["price_id"], rng.randrange(0, 50), ts, rng.choice([None, f"k{rng.randrange(5)}"]))

    def op_change():
        sub = pick(subs)
        if not sub:
            return "skip"
        cus = ctx.store.customers[sub["customer_id"]]
        return engine.change_plan(sub["id"], items_for(cus))

    def op_cancel():
        sub = pick(subs)
        return engine.cancel(sub["id"], at_period_end=rng.random() < 0.6) if sub else "skip"

    def op_pause_resume():
        sub = pick(subs)
        if not sub:
            return "skip"
        return subscriptions.resume(ctx, sub["id"]) if sub["status"] == "paused" else subscriptions.pause(ctx, sub["id"])

    def op_credit():
        cus = pick(custs)
        return ledger.grant_credit(ctx, cus["id"], rng.choice([100, 2500])) if cus else "skip"

    def paid_invoice():
        return pick([inv for inv in ctx.store.invoices.values() if inv["status"] == "paid"])

    def op_refund():
        inv = paid_invoice()
        return engine.refund(inv["id"]) if inv else "skip"

    def op_credit_note():
        inv = paid_invoice()
        return refunds.credit_invoice(ctx, inv["id"], rng.choice([50, 100000]), "goodwill") if inv else "skip"

    def op_void():
        inv = pick([inv for inv in ctx.store.invoices.values() if inv["status"] == "open"])
        return invoices.void(ctx, inv["id"]) if inv else "skip"

    def op_one_off():
        cus = pick(custs)
        if not cus:
            return "skip"
        inv = invoices.create_one_off(ctx, cus["id"], [("setup", rng.choice([500, 1999]), rng.choice([1, 3]))])
        return invoices.finalize(ctx, inv["id"])

    def op_advance():
        clock.advance(rng.choice([3600, 86400, 3 * 86400, 15 * 86400, 31 * 86400]))
        return [inv["id"] for inv in engine.run_cycle()]

    def op_endpoint():
        transport.status_code = rng.choice([200, 200, 500])
        return webhooks.register_endpoint(ctx, "https://h.example.com/" + str(rng.randrange(9)), rng.choice([None, ["invoice.paid"], ["customer.created", "invoice.payment_failed"]]))

    def op_reports():
        now = clock.now()
        return {"mrr": reports.mrr(ctx), "aging": reports.aging(ctx, now), "revenue": reports.revenue_between(ctx, now - 90 * 86400, now + 1),
                "churn": round(reports.churn_rate(ctx, now - 60 * 86400, now), 6)}

    def op_api():
        cus = pick(custs)
        choice = rng.randrange(4)
        if choice == 0:
            return handle(engine, "POST", "/customers", {"email": f"api{len(custs)}@example.com", "name": "Api", "tax_region": "US-CA"})
        if choice == 1 and cus:
            inv = pick(ctx.store.invoices_for(cus["id"]))
            return handle(engine, "GET", f"/invoices/{inv['id'] if inv else 'in_000999'}/text")
        if choice == 2 and subs:
            sub = pick(subs)
            return handle(engine, "POST", "/usage", {"subscription": sub["id"], "price": sub["items"][0]["price_id"], "quantity": 3})
        return handle(engine, "POST", f"/invoices/in_{rng.randrange(1, 30):06d}/refund", {})

    ops = [(op_customer, 3), (op_coupon, 1), (op_subscribe, 4), (op_usage, 4), (op_change, 2), (op_cancel, 1), (op_pause_resume, 1),
           (op_credit, 1), (op_refund, 2), (op_credit_note, 1), (op_void, 1), (op_one_off, 1), (op_advance, 5), (op_endpoint, 1),
           (op_reports, 1), (op_api, 2)]
    names, weights = zip(*ops)
    for _ in range(steps):
        op = rng.choices(names, weights)[0]
        try:
            result = summarize(op())
        except Exception as error:
            result = "ERR:" + type(error).__name__ + ":" + str(error)[:80]
        out.append([op.__name__, result])
    webhooks.deliver_pending(ctx)
    state = {
        "invoices": ctx.store.invoices, "subscriptions": ctx.store.subscriptions, "payments": ctx.store.payments,
        "ledger": ctx.store.ledger, "credit_notes": ctx.store.credit_notes, "usage": ctx.store.usage_records,
        "events": [[e["type"], e["created_at"]] for e in ctx.store.events.values()],
        "deliveries": ctx.store.deliveries, "charges": gateway.charges, "refunds": gateway.refunds,
        "sent": [[url, body.decode()] for url, body, _ in transport.sent],
    }
    return {"seed": seed, "trace": out, "state": summarize(state)}

seeds = [int(s) for s in sys.argv[1].split(",")]
steps = int(sys.argv[2])
for seed in seeds:
    print(json.dumps(run(seed, steps), sort_keys=True))
'''


def traces(package_root: Path, seeds: list[int], steps: int) -> list[str]:
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    proc = subprocess.run(
        [sys.executable, "-c", DRIVER, ",".join(map(str, seeds)), str(steps)],
        env=env, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"driver failed for {package_root}:\n{proc.stderr[-3000:]}")
    return proc.stdout.splitlines()


def first_difference(a: str, b: str) -> str:
    left, right = json.loads(a), json.loads(b)
    for index, (x, y) in enumerate(zip(left["trace"], right["trace"])):
        if x != y:
            return f"step {index}: {json.dumps(x)[:300]}\n     vs   {json.dumps(y)[:300]}"
    for key in left["state"]:
        if left["state"][key] != right["state"].get(key):
            return f"final state differs in {key!r}"
    return "traces differ"


def compare(a: Path, b: Path, seeds: list[int], steps: int) -> list[tuple[int, str]]:
    """Return (seed, description) for every seed whose traces differ."""
    left, right = traces(a, seeds, steps), traces(b, seeds, steps)
    return [(seed, first_difference(x, y)) for seed, x, y in zip(seeds, left, right) if x != y]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reference", type=Path, help="directory containing the reference billing/ package")
    parser.add_argument("candidates", type=Path, nargs="+", help="directories containing billing/ packages to compare")
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--steps", type=int, default=120)
    args = parser.parse_args()
    seeds = list(range(args.seeds))
    failed = 0
    for candidate in args.candidates:
        diffs = compare(args.reference, candidate, seeds, args.steps)
        failed += bool(diffs)
        print(f"{'ok ' if not diffs else 'FAIL'} {candidate}: {len(seeds) - len(diffs)}/{len(seeds)} seeds identical")
        for seed, description in diffs[:3]:
            print(f"   seed {seed}: {description}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
