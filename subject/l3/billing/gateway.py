"""In-process payment gateway and webhook transport used outside production."""

from typing import Final

from .models import Cents, ChargeResult, CurrencyCode, PaymentMethod, RefundResult

DECLINE_CODES: Final[dict[str, str]] = {
    "0002": "card_declined",
    "9995": "insufficient_funds",
    "0069": "expired_card",
}


class FakeGateway:
    def __init__(self) -> None:
        self.charges: list[tuple[str, Cents, CurrencyCode, str]] = []
        self.refunds: list[tuple[str, Cents]] = []
        self._by_key: dict[str, ChargeResult] = {}
        self._captured: dict[str, Cents] = {}
        self.fail_next: list[str] = []

    def charge(
        self, method: PaymentMethod, amount: Cents, currency: CurrencyCode, idempotency_key: str
    ) -> ChargeResult:
        previous = self._by_key.get(idempotency_key)
        if previous is not None:
            return previous
        self.charges.append((method["id"], amount, currency, idempotency_key))
        failure = self.fail_next.pop(0) if self.fail_next else DECLINE_CODES.get(method["last4"])
        if failure is not None:
            result: ChargeResult = {"ok": False, "gateway_ref": None, "failure_code": failure}
        else:
            ref = f"ch_{len(self.charges):06d}"
            self._captured[ref] = amount
            result = {"ok": True, "gateway_ref": ref, "failure_code": None}
        self._by_key[idempotency_key] = result
        return result

    def refund(self, gateway_ref: str, amount: Cents) -> RefundResult:
        captured = self._captured.get(gateway_ref)
        if captured is None or amount > captured:
            return {"ok": False, "gateway_ref": None}
        self._captured[gateway_ref] = Cents(captured - amount)
        self.refunds.append((gateway_ref, amount))
        return {"ok": True, "gateway_ref": f"re_{len(self.refunds):06d}"}


class RecordingTransport:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.sent: list[tuple[str, bytes, dict[str, str]]] = []

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        self.sent.append((url, body, dict(headers)))
        return self.status_code
