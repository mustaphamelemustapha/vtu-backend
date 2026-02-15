import secrets
import time
from dataclasses import dataclass


@dataclass
class ProviderResult:
    success: bool
    external_reference: str | None = None
    message: str | None = None
    meta: dict | None = None


class MockBillsProvider:
    """
    Mock provider used to ship UI + wallet flows without binding to a real VTU aggregator yet.

    Behavior:
    - Always returns success unless the customer identifier starts with "0000" (simulated failure).
    - Exam pins return a generated PIN in meta.
    """

    def _ref(self, prefix: str) -> str:
        return f"{prefix}-MOCK-{int(time.time())}-{secrets.token_hex(3)}"

    def purchase_airtime(self, network: str, phone_number: str, amount: float) -> ProviderResult:
        if str(phone_number).strip().startswith("0000"):
            return ProviderResult(False, message="Mock failure: invalid phone number.")
        return ProviderResult(True, external_reference=self._ref("AIRTIME"), meta={"network": network, "phone_number": phone_number})

    def purchase_cable(self, provider: str, smartcard_number: str, package_code: str, amount: float) -> ProviderResult:
        if str(smartcard_number).strip().startswith("0000"):
            return ProviderResult(False, message="Mock failure: invalid smartcard number.")
        return ProviderResult(True, external_reference=self._ref("CABLE"), meta={"provider": provider, "smartcard_number": smartcard_number, "package_code": package_code})

    def purchase_electricity(self, disco: str, meter_number: str, meter_type: str, amount: float) -> ProviderResult:
        if str(meter_number).strip().startswith("0000"):
            return ProviderResult(False, message="Mock failure: invalid meter number.")
        token = f"{secrets.randbelow(10**12):012d}"
        return ProviderResult(
            True,
            external_reference=self._ref("ELEC"),
            meta={"disco": disco, "meter_number": meter_number, "meter_type": meter_type, "token": token},
        )

    def purchase_exam_pin(self, exam: str, quantity: int, phone_number: str | None = None) -> ProviderResult:
        pins = []
        for _ in range(int(quantity or 1)):
            pins.append(f"{secrets.randbelow(10**12):012d}")
        return ProviderResult(True, external_reference=self._ref("EXAM"), meta={"exam": exam, "pins": pins, "phone_number": phone_number})

