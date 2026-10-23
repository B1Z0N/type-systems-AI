"""In-process payment gateway and webhook transport used outside production."""

# Cards whose last four digits are listed here are always declined with that code.
DECLINE_CODES = {"0002": "card_declined", "9995": "insufficient_funds", "0069": "expired_card"}


class FakeGateway:
    """Deterministic stand-in for a card processor.

    Every charge attempt is recorded in `charges` and every refund in `refunds`. Push
    failure codes onto `fail_next` to make upcoming charges fail regardless of the card.
    """

    def __init__(self):
        self.charges = []
        self.refunds = []
        self._result_by_idempotency_key = {}
        self._captured_by_ref = {}
        self.fail_next = []

    def charge(self, method, amount, currency, idempotency_key):
        """Charge `amount` minor units to `method`.

        Returns {"ok", "gateway_ref", "failure_code"}. Repeating an idempotency key
        returns the first result without charging again.
        """
        previous_result = self._result_by_idempotency_key.get(idempotency_key)
        if previous_result is not None:
            return previous_result
        self.charges.append((method["id"], amount, currency, idempotency_key))
        failure_code = self.fail_next.pop(0) if self.fail_next else DECLINE_CODES.get(method["last4"])
        if failure_code is not None:
            result = {"ok": False, "gateway_ref": None, "failure_code": failure_code}
        else:
            charge_ref = f"ch_{len(self.charges):06d}"
            self._captured_by_ref[charge_ref] = amount
            result = {"ok": True, "gateway_ref": charge_ref, "failure_code": None}
        self._result_by_idempotency_key[idempotency_key] = result
        return result

    def refund(self, gateway_ref, amount):
        """Refund up to the amount still captured on a charge.

        Returns {"ok", "gateway_ref"}; refunds fail for unknown charges or amounts
        larger than what remains captured.
        """
        captured_amount = self._captured_by_ref.get(gateway_ref)
        if captured_amount is None or amount > captured_amount:
            return {"ok": False, "gateway_ref": None}
        self._captured_by_ref[gateway_ref] = captured_amount - amount
        self.refunds.append((gateway_ref, amount))
        return {"ok": True, "gateway_ref": f"re_{len(self.refunds):06d}"}


class RecordingTransport:
    """Webhook transport that records each post and answers with a fixed status code."""

    def __init__(self, status_code=200):
        self.status_code = status_code
        self.sent = []

    def post(self, url, body, headers):
        self.sent.append((url, body, dict(headers)))
        return self.status_code
