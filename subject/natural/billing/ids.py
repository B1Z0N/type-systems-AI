"""Deterministic, prefixed identifier generation."""

PREFIXES = ("cus", "pm", "plan", "price", "sub", "in", "py", "cpn", "ur", "le", "cn", "evt", "we")


class IdGenerator:
    """Hands out ids like "cus_000001", counting independently per prefix.

    Ids are deterministic so that repeated runs (and tests) produce the same output.
    """

    def __init__(self):
        self._counter_by_prefix = {prefix: 0 for prefix in PREFIXES}

    def next(self, prefix):
        """Return the next id for `prefix`; raises ValueError for an unknown prefix."""
        if prefix not in self._counter_by_prefix:
            raise ValueError(f"unknown id prefix: {prefix}")
        self._counter_by_prefix[prefix] += 1
        return f"{prefix}_{self._counter_by_prefix[prefix]:06d}"

    def customer(self):
        return self.next("cus")

    def payment_method(self):
        return self.next("pm")

    def plan(self):
        return self.next("plan")

    def price(self):
        return self.next("price")

    def subscription(self):
        return self.next("sub")

    def invoice(self):
        return self.next("in")

    def payment(self):
        return self.next("py")

    def coupon(self):
        return self.next("cpn")

    def usage_record(self):
        return self.next("ur")

    def ledger_entry(self):
        return self.next("le")

    def credit_note(self):
        return self.next("cn")

    def event(self):
        return self.next("evt")

    def endpoint(self):
        return self.next("we")
