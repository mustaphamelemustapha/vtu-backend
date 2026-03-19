import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.core.config import get_settings


@dataclass
class ProviderResult:
    success: bool
    external_reference: str | None = None
    message: str | None = None
    meta: dict | None = None


settings = get_settings()


def _vtpass_request_id() -> str:
    # VTpass requires first 12 chars to be YYYYMMDDHHMM in Africa/Lagos timezone.
    now = datetime.now(ZoneInfo("Africa/Lagos"))
    prefix = now.strftime("%Y%m%d%H%M")
    return f"{prefix}{secrets.token_hex(6)}"


def _normalize_vtpass_base_url(raw_url: str) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return "https://vtpass.com/api"
    if url.endswith("/"):
        url = url[:-1]
    if url.endswith("/api"):
        return url
    return f"{url}/api"


def _airtime_service_id(network: str) -> str:
    key = str(network or "").strip().lower()
    if key in {"9mobile", "etisalat", "t2"}:
        return "etisalat"
    return key


def _cable_service_id(provider: str) -> str:
    key = str(provider or "").strip().lower()
    if key in {"dstv", "gotv", "startimes", "showmax"}:
        return key
    return key


ELECTRICITY_SERVICE_MAP = {
    "ikeja": "ikeja-electric",
    "eko": "eko-electric",
    "abuja": "abuja-electric",
    "kano": "kano-electric",
    "ibadan": "ibadan-electric",
    "enugu": "enugu-electric",
    "portharcourt": "phed",
    "phed": "phed",
    "jos": "jos-electric",
    "kaduna": "kaduna-electric",
    "benin": "benin-electric",
    "aba": "aba-electric",
    "yola": "yola-electric",
}


def _electricity_service_id(disco: str) -> str:
    key = str(disco or "").strip().lower().replace(" ", "")
    key = key.replace("_", "-")
    if key in ELECTRICITY_SERVICE_MAP:
        return ELECTRICITY_SERVICE_MAP[key]
    if key.endswith("-electric") or key.endswith("-electricity"):
        return key
    return f"{key}-electric"


class VTPassBillsProvider:
    def __init__(self):
        self.base_url = _normalize_vtpass_base_url(str(settings.vtpass_base_url))
        self.api_key = settings.vtpass_api_key or ""
        self.public_key = settings.vtpass_public_key or ""
        self.secret_key = settings.vtpass_secret_key or ""
        self.timeout = settings.vtpass_timeout_seconds

    def _post_headers(self) -> dict:
        return {
            "api-key": self.api_key,
            "secret-key": self.secret_key,
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(url, json=payload, headers=self._post_headers())
        data = res.json() if res.content else {}
        if res.status_code >= 400:
            message = data.get("response_description") or data.get("message") or "VTpass error"
            raise RuntimeError(message)
        return data

    def _parse_result(self, data: dict) -> ProviderResult:
        code = str(data.get("code") or data.get("response_description") or "").strip()
        content = data.get("content") or {}
        tx = content.get("transactions") or {}
        status = str(tx.get("status") or "").lower()
        ok = code == "000" or status in {"delivered", "successful"}
        external_reference = str(data.get("requestId") or "") or str(tx.get("transactionId") or "") or None
        purchased_code = data.get("purchased_code")
        token = None
        if isinstance(purchased_code, str):
            token = purchased_code.split(":")[-1].strip() if ":" in purchased_code else purchased_code.strip()
        meta = {
            "vtpass": {
                "status": status,
                "transaction_id": tx.get("transactionId"),
                "product_name": tx.get("product_name"),
                "unique_element": tx.get("unique_element"),
                "purchased_code": purchased_code,
            }
        }
        if token:
            meta["token"] = token
        if ok:
            return ProviderResult(True, external_reference=external_reference, meta=meta)
        message = data.get("response_description") or tx.get("status") or "Provider failed"
        return ProviderResult(False, external_reference=external_reference, message=message, meta=meta)

    def purchase_airtime(self, network: str, phone_number: str, amount: float) -> ProviderResult:
        payload = {
            "request_id": _vtpass_request_id(),
            "serviceID": _airtime_service_id(network),
            "amount": float(amount),
            "phone": str(phone_number),
        }
        data = self._post("/pay", payload)
        return self._parse_result(data)

    def purchase_cable(self, provider: str, smartcard_number: str, package_code: str, amount: float, phone_number: str) -> ProviderResult:
        payload = {
            "request_id": _vtpass_request_id(),
            "serviceID": _cable_service_id(provider),
            "billersCode": str(smartcard_number),
            "variation_code": str(package_code),
            "amount": float(amount),
            "phone": str(phone_number),
        }
        data = self._post("/pay", payload)
        return self._parse_result(data)

    def purchase_electricity(self, disco: str, meter_number: str, meter_type: str, amount: float, phone_number: str) -> ProviderResult:
        payload = {
            "request_id": _vtpass_request_id(),
            "serviceID": _electricity_service_id(disco),
            "billersCode": str(meter_number),
            "variation_code": str(meter_type).lower(),
            "amount": float(amount),
            "phone": str(phone_number),
        }
        data = self._post("/pay", payload)
        return self._parse_result(data)

    def purchase_exam_pin(self, exam: str, quantity: int, phone_number: str | None = None) -> ProviderResult:
        # Exam pin vending varies per product; keep mock behavior until UI collects VTPass fields.
        return MockBillsProvider().purchase_exam_pin(exam, quantity, phone_number)


def get_bills_provider():
    if settings.vtpass_enabled and settings.vtpass_api_key and settings.vtpass_secret_key:
        return VTPassBillsProvider()
    return MockBillsProvider()


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

    def purchase_cable(self, provider: str, smartcard_number: str, package_code: str, amount: float, phone_number: str | None = None) -> ProviderResult:
        if str(smartcard_number).strip().startswith("0000"):
            return ProviderResult(False, message="Mock failure: invalid smartcard number.")
        return ProviderResult(True, external_reference=self._ref("CABLE"), meta={"provider": provider, "smartcard_number": smartcard_number, "package_code": package_code})

    def purchase_electricity(self, disco: str, meter_number: str, meter_type: str, amount: float, phone_number: str | None = None) -> ProviderResult:
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
