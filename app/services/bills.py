import base64
import logging
import re
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
logger = logging.getLogger(__name__)

_VTPASS_SUCCESS_STATUS = {"delivered", "successful", "success", "completed", "done"}
_VTPASS_PENDING_STATUS = {"pending", "processing", "queued", "in_progress", "submitted", "accepted"}
_VTPASS_FAILURE_STATUS = {"failed", "fail", "error", "rejected", "declined", "cancelled", "canceled"}

_EXAM_SERVICE_CONFIG: dict[str, dict[str, list[str]]] = {
    "waec": {
        "service_ids": ["waec", "waec-registration"],
        "variation_hints": ["waecdirect", "waec-registraion", "waec-registration", "wassce"],
    },
    "neco": {
        "service_ids": ["neco"],
        "variation_hints": ["neco"],
    },
    "jamb": {
        "service_ids": ["jamb"],
        "variation_hints": ["utme-no-mock", "utme-mock", "utme", "de"],
    },
}

_PIN_RE = re.compile(r"\bpin\b\s*[:=]\s*([A-Za-z0-9-]+)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\btoken\b\s*[:=]\s*([A-Za-z0-9-]+)", re.IGNORECASE)


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


def _data_service_id(network: str) -> str:
    key = str(network or "").strip().lower()
    if key in {"9mobile", "etisalat", "t2"}:
        return "etisalat-data"
    if key in {"mtn", "glo", "airtel"}:
        return f"{key}-data"
    return f"{key}-data"


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


def _normalize_exam_key(exam: str) -> str:
    key = str(exam or "").strip().lower().replace("_", "-")
    if key in {"waec-result", "waec-result-checker", "waec-direct", "waecdirect"}:
        return "waec"
    if key in {"waec-registration", "waec-registration-pin", "waec-reg"}:
        return "waec"
    if key in {"neco-result", "neco-result-checker", "neco-token"}:
        return "neco"
    if key in {"jamb-pin", "jamb-utme", "utme"}:
        return "jamb"
    return key


def _extract_purchased_pins(data: dict) -> list[str]:
    pins: list[str] = []
    cards = data.get("cards")
    if isinstance(cards, list):
        for card in cards:
            if not isinstance(card, dict):
                continue
            pin = str(card.get("Pin") or card.get("pin") or card.get("code") or "").strip()
            if pin:
                pins.append(pin)

    purchased_code = str(data.get("purchased_code") or "").strip()
    if purchased_code:
        for match in _PIN_RE.finditer(purchased_code):
            pin = str(match.group(1) or "").strip()
            if pin:
                pins.append(pin)

    # Preserve order, remove duplicates.
    return list(dict.fromkeys(pins))


def _extract_token(purchased_code: str | None) -> str | None:
    text = str(purchased_code or "").strip()
    if not text:
        return None
    token_match = _TOKEN_RE.search(text)
    if token_match:
        return str(token_match.group(1) or "").strip() or None
    if ":" in text:
        return text.split(":")[-1].strip() or None
    return text or None


class VTPassBillsProvider:
    def __init__(self):
        self.base_url = _normalize_vtpass_base_url(str(settings.vtpass_base_url))
        self.api_key = settings.vtpass_api_key or ""
        self.public_key = settings.vtpass_public_key or ""
        self.secret_key = settings.vtpass_secret_key or ""
        self.timeout = settings.vtpass_timeout_seconds

    def _post_headers(self) -> dict:
        headers = {
            "api-key": self.api_key,
            "secret-key": self.secret_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key and self.secret_key:
            basic = base64.b64encode(f"{self.api_key}:{self.secret_key}".encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {basic}"
        return headers

    def _get_headers(self) -> dict:
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.public_key:
            headers["public-key"] = self.public_key
        if self.secret_key:
            headers["secret-key"] = self.secret_key
        if self.api_key and self.secret_key:
            basic = base64.b64encode(f"{self.api_key}:{self.secret_key}".encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {basic}"
        return headers

    @staticmethod
    def _safe_json(res: httpx.Response) -> dict:
        if not res.content:
            return {}
        try:
            payload = res.json()
        except Exception:
            return {"message": res.text}
        return payload if isinstance(payload, dict) else {"message": str(payload)}

    @staticmethod
    def _error_message(data: dict, fallback: str) -> str:
        return str(
            data.get("response_description")
            or data.get("message")
            or data.get("error")
            or data.get("detail")
            or fallback
        ).strip()

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(url, json=payload, headers=self._post_headers())
        except Exception as exc:
            raise RuntimeError(f"VTPass network error: {exc}") from exc
        data = self._safe_json(res)
        if res.status_code >= 400:
            message = self._error_message(data, "VTpass error")
            raise RuntimeError(f"VTpass HTTP {res.status_code}: {message}")
        return data

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(url, params=params, headers=self._get_headers())
        except Exception as exc:
            raise RuntimeError(f"VTPass network error: {exc}") from exc
        data = self._safe_json(res)
        if res.status_code >= 400:
            message = self._error_message(data, "VTpass error")
            raise RuntimeError(f"VTpass HTTP {res.status_code}: {message}")
        return data

    def _parse_result(self, data: dict) -> ProviderResult:
        raw_code = str(data.get("code") or "").strip()
        code = raw_code.lower()
        response_description = str(data.get("response_description") or data.get("message") or "").strip()
        response_description_lower = response_description.lower()
        content = data.get("content") or {}
        tx = content.get("transactions") or {}
        status = str(tx.get("status") or data.get("status") or "").strip().lower()
        status_success = status in _VTPASS_SUCCESS_STATUS
        status_pending = status in _VTPASS_PENDING_STATUS
        status_failed = status in _VTPASS_FAILURE_STATUS
        response_success = any(word in response_description_lower for word in ("successful", "success", "delivered"))
        response_pending = "pending" in response_description_lower
        response_failed = any(word in response_description_lower for word in ("fail", "error", "declined", "rejected"))

        ok = status_success or (code == "000" and not status_pending and not status_failed and not response_failed)
        pending = status_pending or response_pending
        external_reference = str(data.get("requestId") or "") or str(tx.get("transactionId") or "") or None
        purchased_code = data.get("purchased_code")
        pins = _extract_purchased_pins(data)
        token = _extract_token(purchased_code if isinstance(purchased_code, str) else None)
        meta = {
            "vtpass": {
                "status": status,
                "code": raw_code,
                "response_description": response_description,
                "transaction_id": tx.get("transactionId"),
                "product_name": tx.get("product_name"),
                "unique_element": tx.get("unique_element"),
                "purchased_code": purchased_code,
            }
        }
        if pins:
            meta["pins"] = pins
        if token:
            meta["token"] = token
        if ok:
            return ProviderResult(True, external_reference=external_reference, meta=meta)
        if pending and not status_failed and not response_failed:
            return ProviderResult(False, external_reference=external_reference, message="Transaction pending", meta=meta)
        message = response_description or str(tx.get("status") or "").strip() or "Provider failed"
        if response_success and not response_failed:
            message = "Transaction successful"
        return ProviderResult(False, external_reference=external_reference, message=message, meta=meta)

    def _extract_variations(self, data: dict) -> list[dict]:
        content = data.get("content") or {}
        variations = content.get("variations") or content.get("varations") or content.get("variation") or []
        if isinstance(variations, dict):
            return [item for item in variations.values() if isinstance(item, dict)]
        if isinstance(variations, list):
            return [item for item in variations if isinstance(item, dict)]
        return []

    def _pick_variation_code(self, variations: list[dict], hints: list[str]) -> str | None:
        if not variations:
            return None
        lower_hints = [h.lower() for h in hints]
        for variation in variations:
            code = str(variation.get("variation_code") or variation.get("code") or "").strip()
            name = str(variation.get("name") or "").strip().lower()
            if not code:
                continue
            code_lower = code.lower()
            if any(h in code_lower or h in name for h in lower_hints):
                return code
        for variation in variations:
            code = str(variation.get("variation_code") or variation.get("code") or "").strip()
            if code:
                return code
        return None

    def _service_matches_exam(self, exam_key: str, service_id: str, service_name: str) -> bool:
        text = f"{service_id} {service_name}".lower()
        if exam_key == "waec":
            return "waec" in text
        if exam_key == "neco":
            return "neco" in text
        if exam_key == "jamb":
            return "jamb" in text
        return exam_key in text

    def _resolve_exam_service_and_variation(self, exam: str) -> tuple[str | None, str | None]:
        exam_key = _normalize_exam_key(exam)
        config = _EXAM_SERVICE_CONFIG.get(exam_key, {"service_ids": [exam_key], "variation_hints": [exam_key]})
        candidate_ids = list(dict.fromkeys(config.get("service_ids", []) + [exam_key]))
        variation_hints = config.get("variation_hints", [exam_key])

        for service_id in candidate_ids:
            try:
                data = self._get("/service-variations", params={"serviceID": service_id})
            except Exception:
                continue
            variations = self._extract_variations(data)
            if not variations:
                continue
            code = self._pick_variation_code(variations, variation_hints)
            return service_id, code

        try:
            services_data = self._get("/services", params={"identifier": "education"})
            services = services_data.get("content") or []
        except Exception:
            services = []
        if isinstance(services, list):
            for item in services:
                if not isinstance(item, dict):
                    continue
                service_id = str(item.get("serviceID") or item.get("service_id") or "").strip()
                service_name = str(item.get("name") or "").strip()
                if not service_id or not self._service_matches_exam(exam_key, service_id, service_name):
                    continue
                try:
                    data = self._get("/service-variations", params={"serviceID": service_id})
                except Exception:
                    continue
                code = self._pick_variation_code(self._extract_variations(data), variation_hints)
                return service_id, code
        return None, None

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
        phone = str(phone_number or "").strip()
        if not phone:
            return ProviderResult(False, message="Phone number is required for exam pin purchase.")
        exam_key = _normalize_exam_key(exam)
        service_id, variation_code = self._resolve_exam_service_and_variation(exam_key)
        if not service_id:
            return ProviderResult(False, message=f"Unsupported exam type: {exam}")

        payload = {
            "request_id": _vtpass_request_id(),
            "serviceID": service_id,
            "quantity": max(1, int(quantity or 1)),
            "phone": phone,
        }
        if variation_code:
            payload["variation_code"] = variation_code
        if exam_key == "jamb":
            # JAMB purchases require billersCode (profile ID). Until UI captures it,
            # we use the provided phone field as a fallback input.
            payload["billersCode"] = phone

        data = self._post("/pay", payload)
        result = self._parse_result(data)
        if result.meta is None:
            result.meta = {}
        result.meta.setdefault("vtpass", {})
        result.meta["vtpass"]["exam"] = exam_key
        result.meta["vtpass"]["service_id"] = service_id
        result.meta["vtpass"]["variation_code"] = variation_code
        if "pins" not in result.meta:
            pins = _extract_purchased_pins(data)
            if pins:
                result.meta["pins"] = pins
        return result

    def fetch_data_variations(self, network: str) -> list[dict]:
        service_id = _data_service_id(network)
        data = self._get("/service-variations", params={"serviceID": service_id})
        return self._extract_variations(data)

    def purchase_data(
        self,
        network: str,
        phone_number: str,
        plan_code: str,
        amount: float | None = None,
        request_id: str | None = None,
    ) -> ProviderResult:
        payload = {
            "request_id": request_id or _vtpass_request_id(),
            "serviceID": _data_service_id(network),
            "variation_code": str(plan_code),
            "billersCode": str(phone_number),
            "phone": str(phone_number),
        }
        if amount is not None:
            payload["amount"] = float(amount)
        data = self._post("/pay", payload)
        result = self._parse_result(data)
        if result.meta is not None:
            result.meta.setdefault("vtpass", {})
            result.meta["vtpass"]["request_id"] = payload["request_id"]
            result.meta["vtpass"]["service_id"] = payload["serviceID"]
            result.meta["vtpass"]["variation_code"] = payload["variation_code"]
        return result


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
