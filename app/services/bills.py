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

_CLUBKONNECT_SUCCESS_CODES = {100, 199, 200, 201, 300}
_CLUBKONNECT_PENDING_CODES = {100, 201, 300, 600, 601, 602, 603, 604, 605, 606}
_CLUBKONNECT_FAILURE_CODES = {
    399,
    400,
    401,
    402,
    403,
    404,
    405,
    406,
    407,
    408,
    409,
    410,
    411,
    412,
    413,
    414,
    415,
    416,
    417,
    418,
    499,
    500,
    501,
    506,
    507,
    508,
    509,
    510,
    511,
    512,
    599,
    699,
}

_CLUBKONNECT_NETWORK_MAP = {
    "mtn": "01",
    "glo": "02",
    "9mobile": "03",
    "etisalat": "03",
    "t2": "03",
    "airtel": "04",
}

_CLUBKONNECT_CABLE_MAP = {
    "dstv": "DStv",
    "gotv": "GOtv",
    "startimes": "Startimes",
    "showmax": "Showmax",
}

_CLUBKONNECT_DISCO_CODE_MAP = {
    "ekedc": "01",
    "eko": "01",
    "ikedc": "02",
    "ikeja": "02",
    "aedc": "03",
    "abuja": "03",
    "kedco": "04",
    "kano": "04",
    "phed": "05",
    "portharcourt": "05",
    "jos": "06",
    "jed": "06",
    "ibedc": "07",
    "ibadan": "07",
    "kaedco": "08",
    "kaduna": "08",
    "eedc": "09",
    "enugu": "09",
    "bedc": "10",
    "benin": "10",
    "yedc": "11",
    "yola": "11",
    "aple": "12",
    "aba": "12",
}

_CLUBKONNECT_DISCO_NAME_BY_CODE = {
    "01": "ekedc",
    "02": "ikedc",
    "03": "aedc",
    "04": "kedco",
    "05": "phed",
    "06": "jos",
    "07": "ibedc",
    "08": "kaedco",
    "09": "eedc",
    "10": "bedc",
    "11": "yedc",
    "12": "aple",
}


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


def _normalize_clubkonnect_base_url(raw_url: str) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return "https://www.nellobytesystems.com/"
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"
    if not url.endswith("/"):
        url = f"{url}/"
    return url


def _clubkonnect_request_id(prefix: str = "AXIS") -> str:
    now = datetime.now(ZoneInfo("Africa/Lagos")).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{now}-{secrets.token_hex(3)}"


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

    def purchase_exam_pin(
        self,
        exam: str,
        quantity: int,
        phone_number: str | None = None,
        exam_type: str | None = None,
    ) -> ProviderResult:
        phone = str(phone_number or "").strip()
        if not phone:
            return ProviderResult(False, message="Phone number is required for exam pin purchase.")
        exam_key = _normalize_exam_key(exam)
        service_id, variation_code = self._resolve_exam_service_and_variation(exam_key)
        if exam_type:
            variation_code = str(exam_type).strip()
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

    def fetch_exam_packages(self, exam: str) -> list[dict]:
        exam_key = _normalize_exam_key(exam)
        service_id, _ = self._resolve_exam_service_and_variation(exam_key)
        if not service_id:
            return []
        try:
            data = self._get("/service-variations", params={"serviceID": service_id})
        except Exception:
            return []
        rows = self._extract_variations(data)
        packages: list[dict] = []
        for row in rows:
            code = str(row.get("variation_code") or row.get("code") or "").strip()
            name = str(row.get("name") or row.get("variation_name") or code).strip()
            amount_raw = row.get("variation_amount") or row.get("amount")
            try:
                amount = float(str(amount_raw).replace(",", "").strip()) if amount_raw not in (None, "") else None
            except Exception:
                amount = None
            if not code:
                continue
            packages.append({"code": code, "name": name or code, "amount": amount, "exam": exam_key})
        return packages

    def fetch_cable_packages(self, provider: str) -> list[dict]:
        service_id = _cable_service_id(provider)
        data = self._get("/service-variations", params={"serviceID": service_id})
        rows = self._extract_variations(data)
        packages: list[dict] = []
        for row in rows:
            code = str(row.get("variation_code") or row.get("code") or "").strip()
            name = str(row.get("name") or code).strip()
            amount_raw = row.get("variation_amount") or row.get("amount")
            try:
                amount = float(str(amount_raw).replace(",", "").strip()) if amount_raw not in (None, "") else None
            except Exception:
                amount = None
            if not code:
                continue
            packages.append({"code": code, "name": name, "amount": amount, "provider": str(provider or "").strip().lower()})
        return packages

    def verify_cable_customer(self, provider: str, smartcard_number: str) -> dict:
        try:
            data = self._post(
                "/merchant-verify",
                {"billersCode": str(smartcard_number).strip(), "serviceID": _cable_service_id(provider)},
            )
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

        content = data.get("content") or {}
        customer_name = (
            content.get("Customer_Name")
            or content.get("customer_name")
            or content.get("name")
            or data.get("customer_name")
            or data.get("message")
            or ""
        )
        customer_name = str(customer_name or "").strip()
        if customer_name:
            return {"ok": True, "customer_name": customer_name}
        return {"ok": False, "message": "Unable to verify smartcard right now."}

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


class ClubKonnectBillsProvider:
    def __init__(self):
        self.base_url = _normalize_clubkonnect_base_url(str(settings.clubkonnect_base_url or ""))
        self.user_id = str(settings.nello_user_id or settings.clubkonnect_user_id or "").strip()
        self.api_key = str(settings.nello_api_key or settings.clubkonnect_api_key or "").strip()
        self.timeout = settings.clubkonnect_timeout_seconds

    def _callback_url(self) -> str:
        callback = str(settings.clubkonnect_callback_url or "").strip()
        if callback:
            return callback
        base = str(settings.frontend_base_url or "").strip().rstrip("/")
        if not base:
            return "https://axisvtu.com/app/transactions"
        return f"{base}/app/transactions"

    @staticmethod
    def _safe_json(res: httpx.Response) -> dict:
        if not res.content:
            return {}
        try:
            payload = res.json()
        except Exception:
            return {"message": res.text}
        return payload if isinstance(payload, dict) else {"message": str(payload)}

    def _request(self, endpoint: str, params: dict) -> dict:
        if not self.user_id or not self.api_key:
            raise RuntimeError("ClubKonnect credentials are missing.")
        payload = {
            **(params or {}),
            "UserID": self.user_id,
            "APIKey": self.api_key,
        }
        url = f"{self.base_url}{endpoint.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(url, params=payload)
        except Exception as exc:
            raise RuntimeError(f"ClubKonnect network error: {exc}") from exc
        data = self._safe_json(res)
        if res.status_code >= 400:
            message = str(data.get("message") or data.get("status") or "ClubKonnect error")
            raise RuntimeError(f"ClubKonnect HTTP {res.status_code}: {message}")
        return data

    @staticmethod
    def _status_code_and_text(data: dict) -> tuple[int | None, str]:
        raw_code = data.get("statuscode") or data.get("status_code") or data.get("StatusCode")
        code_text = str(raw_code or "").strip()
        code = int(code_text) if code_text.isdigit() else None

        raw_status = data.get("orderstatus") or data.get("status") or data.get("Status")
        status_text = str(raw_status or "").strip().upper()

        # Some responses return only numeric status in `status`.
        if not status_text and code is not None:
            status_text = str(code)
        if code is None and status_text.isdigit():
            code = int(status_text)
        return code, status_text

    @staticmethod
    def _extract_reference(data: dict) -> str | None:
        for key in ("OrderID", "orderid", "order_id", "RequestID", "requestid", "request_id", "reference"):
            val = str(data.get(key) or "").strip()
            if val:
                return val
        return None

    @staticmethod
    def _extract_exam_pins(data: dict) -> list[str]:
        pins: list[str] = []
        for key in ("pin", "Pin", "pins", "Pins", "Token", "token"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                pins.append(value.strip())
            if isinstance(value, list):
                for item in value:
                    text = str(item or "").strip()
                    if text:
                        pins.append(text)
        return list(dict.fromkeys(pins))

    def _parse_result(self, data: dict, action: str) -> ProviderResult:
        code, status_text = self._status_code_and_text(data)
        external_reference = self._extract_reference(data)
        message = str(
            data.get("remark")
            or data.get("message")
            or data.get("orderstatus")
            or data.get("status")
            or ""
        ).strip()

        pending = (
            (code in _CLUBKONNECT_PENDING_CODES if code is not None else False)
            or status_text in {"ORDER_RECEIVED", "ORDER_PROCESSED", "ORDER_ONHOLD", "PENDING"}
        )
        success = (
            (code in _CLUBKONNECT_SUCCESS_CODES if code is not None else False)
            or status_text in {"ORDER_COMPLETED", "SUCCESS", "TRANSACTION_SUCCESSFUL"}
        )
        failed = (
            (code in _CLUBKONNECT_FAILURE_CODES if code is not None else False)
            or "INVALID" in status_text
            or "ERROR" in status_text
            or "FAILED" in status_text
            or "CANCEL" in status_text
        )

        meta = {
            "clubkonnect": {
                "status": "pending" if pending else ("success" if success and not failed else "failed"),
                "raw_status": str(data.get("status") or ""),
                "code": code,
                "action": action,
                "raw": data,
            }
        }

        if pending and not failed:
            return ProviderResult(False, external_reference=external_reference, message="Transaction pending", meta=meta)
        if success and not failed:
            return ProviderResult(True, external_reference=external_reference, message=message or "Successful", meta=meta)
        return ProviderResult(False, external_reference=external_reference, message=message or "Provider failed", meta=meta)

    def _query_transaction(self, *, order_id: str | None = None, request_id: str | None = None) -> dict | None:
        params: dict[str, str] = {}
        if order_id:
            params["OrderID"] = str(order_id)
        elif request_id:
            params["RequestID"] = str(request_id)
        else:
            return None
        try:
            return self._request("APIQueryV1.asp", params)
        except Exception as exc:
            logger.warning("ClubKonnect query failed order_id=%s request_id=%s error=%s", order_id, request_id, exc)
            return None

    def _settle_pending(self, result: ProviderResult, action: str, *, request_id: str | None = None) -> ProviderResult:
        status = str((result.meta or {}).get("clubkonnect", {}).get("status") or "").strip().lower()
        if status != "pending":
            return result
        order_id = str(result.external_reference or "").strip() or None
        for delay in (0.6, 1.2):
            time.sleep(delay)
            queried = self._query_transaction(order_id=order_id, request_id=request_id)
            if not queried:
                continue
            follow_up = self._parse_result(queried, action=action)
            follow_status = str((follow_up.meta or {}).get("clubkonnect", {}).get("status") or "").strip().lower()
            if follow_status != "pending":
                return follow_up
            if not order_id:
                order_id = str(follow_up.external_reference or "").strip() or None
        return result

    @staticmethod
    def _network_code(network: str) -> str:
        key = str(network or "").strip().lower()
        return _CLUBKONNECT_NETWORK_MAP.get(key, key)

    @staticmethod
    def _cable_code(provider: str) -> str:
        key = str(provider or "").strip().lower()
        return _CLUBKONNECT_CABLE_MAP.get(key, provider)

    @staticmethod
    def _cable_provider_key(value: str) -> str:
        key = str(value or "").strip().lower()
        if not key:
            return ""
        if key in {"dstv", "d_stv", "d-stv"}:
            return "dstv"
        if key in {"gotv", "go_tv", "go-tv"}:
            return "gotv"
        if key in {"startimes", "star_times", "star-times"}:
            return "startimes"
        if key in {"showmax"}:
            return "showmax"
        return key

    @staticmethod
    def _flatten_cable_package_rows(payload: dict) -> list[dict]:
        rows: list[dict] = []
        possible_rows = payload.get("TV_ID") or payload.get("tv_id") or payload.get("data") or payload.get("Data") or payload
        if isinstance(possible_rows, list):
            for item in possible_rows:
                if not isinstance(item, dict):
                    continue
                products = item.get("PRODUCT") or item.get("product")
                if isinstance(products, list):
                    for product in products:
                        if not isinstance(product, dict):
                            continue
                        merged = dict(product)
                        merged.setdefault("provider_hint", item.get("ID") or item.get("provider") or item.get("provider_hint"))
                        rows.append(merged)
                else:
                    rows.append(item)
        elif isinstance(possible_rows, dict):
            for key, value in possible_rows.items():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            products = item.get("PRODUCT") or item.get("product")
                            if isinstance(products, list):
                                for product in products:
                                    if not isinstance(product, dict):
                                        continue
                                    merged = dict(product)
                                    merged.setdefault("provider_hint", item.get("ID") or key)
                                    rows.append(merged)
                            else:
                                enriched = dict(item)
                                enriched.setdefault("provider_hint", key)
                                rows.append(enriched)
                elif isinstance(value, dict):
                    enriched = dict(value)
                    enriched.setdefault("provider_hint", key)
                    rows.append(enriched)
        return rows

    @staticmethod
    def _disco_code(disco: str) -> str:
        key = str(disco or "").strip().lower().replace(" ", "")
        return _CLUBKONNECT_DISCO_CODE_MAP.get(key, disco)

    @staticmethod
    def _meter_code(meter_type: str) -> str:
        key = str(meter_type or "").strip().lower()
        if key == "prepaid":
            return "01"
        if key == "postpaid":
            return "02"
        return meter_type

    @staticmethod
    def _normalize_network_code(value) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return ""
        if raw.isdigit():
            return raw.zfill(2) if len(raw) == 1 else raw
        return _CLUBKONNECT_NETWORK_MAP.get(raw, raw)

    def _flatten_data_plan_rows(self, payload: dict) -> list[dict]:
        rows = payload.get("MOBILE_NETWORK") or payload.get("mobile_network") or payload.get("data") or payload.get("Data") or []
        flattened: list[dict] = []

        # ClubKonnect V1/V2 often returns:
        # {
        #   "MOBILE_NETWORK": {
        #     "Airtel": [{"ID":"04","PRODUCT":[{"PRODUCT_ID":"499.91",...}]}],
        #     ...
        #   }
        # }
        if isinstance(rows, dict):
            for network_name, groups in rows.items():
                if not isinstance(groups, list):
                    continue
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    network_hint = (
                        group.get("ID")
                        or group.get("MobileNetwork")
                        or group.get("mobile_network")
                        or network_name
                    )
                    products = group.get("PRODUCT") or group.get("product") or []
                    if not isinstance(products, list):
                        continue
                    for product in products:
                        if not isinstance(product, dict):
                            continue
                        flattened.append(
                            {
                                "MobileNetwork": network_hint,
                                "DataPlan": product.get("PRODUCT_ID") or product.get("DataPlan") or product.get("dataplan"),
                                "Amount": product.get("PRODUCT_AMOUNT") or product.get("Amount"),
                                "DataType": product.get("PRODUCT_NAME") or product.get("DataType") or product.get("name"),
                                "PRODUCT_CODE": product.get("PRODUCT_CODE"),
                                "PRODUCT_SNO": product.get("PRODUCT_SNO"),
                            }
                        )
            return flattened

        if isinstance(rows, list):
            nested_keys = ("DataPlans", "dataplans", "plans", "Plans", "bundles", "Bundles", "PRODUCT", "product")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                nested = None
                for key in nested_keys:
                    maybe = row.get(key)
                    if isinstance(maybe, list):
                        nested = maybe
                        break
                if nested is None:
                    flattened.append(row)
                    continue

                network_hint = (
                    row.get("MobileNetwork")
                    or row.get("mobile_network")
                    or row.get("ID")
                    or row.get("id")
                    or row.get("Network")
                    or row.get("network")
                )
                for item in nested:
                    if not isinstance(item, dict):
                        continue
                    merged = dict(item)
                    if network_hint not in (None, ""):
                        merged.setdefault("MobileNetwork", network_hint)
                    flattened.append(merged)
        return flattened

    def purchase_airtime(self, network: str, phone_number: str, amount: float) -> ProviderResult:
        request_id = _clubkonnect_request_id("AIRTIME")
        data = self._request(
            "APIAirtimeV1.asp",
            {
                "MobileNetwork": self._network_code(network),
                "Amount": int(float(amount)),
                "MobileNumber": str(phone_number),
                "RequestID": request_id,
                "CallBackURL": self._callback_url(),
            },
        )
        return self._settle_pending(self._parse_result(data, action="airtime"), "airtime", request_id=request_id)

    def purchase_cable(self, provider: str, smartcard_number: str, package_code: str, amount: float, phone_number: str | None = None) -> ProviderResult:
        request_id = _clubkonnect_request_id("CABLE")
        data = self._request(
            "APICableTVV1.asp",
            {
                "CableTV": self._cable_code(provider),
                "Package": str(package_code),
                "SmartCardNo": str(smartcard_number),
                "PhoneNo": str(phone_number or "").strip(),
                "RequestID": request_id,
                "CallBackURL": self._callback_url(),
            },
        )
        return self._settle_pending(self._parse_result(data, action="cable"), "cable", request_id=request_id)

    def fetch_cable_packages(self, provider: str) -> list[dict]:
        key = self._cable_provider_key(provider)
        data = self._request("APICableTVPackagesV2.asp", {})
        rows = self._flatten_cable_package_rows(data)
        packages: list[dict] = []
        for row in rows:
            provider_hint = self._cable_provider_key(
                row.get("CableTV")
                or row.get("cabletv")
                or row.get("TV_ID")
                or row.get("tv_id")
                or row.get("provider")
                or row.get("provider_hint")
            )
            if key and provider_hint and provider_hint != key:
                continue
            code = str(
                row.get("Package")
                or row.get("package")
                or row.get("PACKAGE_ID")
                or row.get("PACKAGE_CODE")
                or row.get("PRODUCT_ID")
                or row.get("ID")
                or row.get("code")
                or ""
            ).strip()
            name = str(
                row.get("PackageName")
                or row.get("package_name")
                or row.get("PACKAGE_NAME")
                or row.get("DESCRIPTION")
                or row.get("PRODUCT_NAME")
                or row.get("DataType")
                or row.get("name")
                or code
            ).strip()
            amount_raw = row.get("Amount") or row.get("amount") or row.get("PACKAGE_AMOUNT") or row.get("PRODUCT_AMOUNT") or row.get("Price")
            try:
                amount = float(str(amount_raw).replace(",", "").strip()) if amount_raw not in (None, "") else None
            except Exception:
                amount = None
            if not code:
                continue
            packages.append(
                {
                    "code": code,
                    "name": name or code,
                    "amount": amount,
                    "provider": provider_hint or key or str(provider or "").strip().lower(),
                }
            )
        dedup: dict[str, dict] = {}
        for item in packages:
            dedup[item["code"]] = item
        return list(dedup.values())

    def verify_cable_customer(self, provider: str, smartcard_number: str) -> dict:
        data = self._request(
            "APIVerifyCableTVV1.0.asp",
            {"CableTV": self._cable_code(provider), "SmartCardNo": str(smartcard_number).strip()},
        )
        customer_name = str(data.get("customer_name") or data.get("CustomerName") or "").strip()
        if customer_name and customer_name.upper() not in {"INVALID_SMARTCARDNO", "INVALID"}:
            return {"ok": True, "customer_name": customer_name}
        return {"ok": False, "message": customer_name or "Unable to verify smartcard number."}

    def purchase_electricity(self, disco: str, meter_number: str, meter_type: str, amount: float, phone_number: str | None = None) -> ProviderResult:
        request_id = _clubkonnect_request_id("ELEC")
        data = self._request(
            "APIElectricityV1.asp",
            {
                "ElectricCompany": self._disco_code(disco),
                "MeterNo": str(meter_number),
                "Amount": int(float(amount)),
                "MeterType": self._meter_code(meter_type),
                "PhoneNo": str(phone_number or "").strip(),
                "RequestID": request_id,
                "CallBackURL": self._callback_url(),
            },
        )
        result = self._settle_pending(self._parse_result(data, action="electricity"), "electricity", request_id=request_id)
        token = str(data.get("token") or data.get("Token") or data.get("metertoken") or "").strip()
        if token:
            result.meta = {**(result.meta or {}), "token": token}
        return result

    def fetch_electricity_discos(self) -> list[dict]:
        data = self._request("APIElectricityDiscosV2.asp", {})
        rows = (
            data.get("ELECTRIC_COMPANY")
            or data.get("electric_company")
            or data.get("ElectricityDiscos")
            or data.get("electricity_discos")
            or data.get("data")
            or data.get("Data")
            or data
        )
        flattened: list[dict] = []
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    flattened.append(row)
        elif isinstance(rows, dict):
            for value in rows.values():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            flattened.append(item)
                elif isinstance(value, dict):
                    flattened.append(value)

        discos: list[dict] = []
        for row in flattened:
            code = str(row.get("ID") or row.get("id") or row.get("code") or row.get("Code") or "").strip()
            name = str(
                row.get("NAME")
                or row.get("name")
                or row.get("Disco")
                or row.get("disco")
                or row.get("ElectricCompany")
                or row.get("electric_company")
                or ""
            ).strip()
            key = ""
            if code and code in _CLUBKONNECT_DISCO_NAME_BY_CODE:
                key = _CLUBKONNECT_DISCO_NAME_BY_CODE[code]
            if not key and name:
                key = str(name).strip().lower().replace(" ", "")
            if not key and code:
                key = code
            if key:
                discos.append(
                    {
                        "code": code or self._disco_code(key),
                        "id": key,
                        "name": name or key.upper(),
                    }
                )

        if not discos:
            for key, code in _CLUBKONNECT_DISCO_CODE_MAP.items():
                if len(code) != 2:
                    continue
                if key not in {"ekedc", "ikedc", "aedc", "kedco", "phed", "jos", "ibedc", "kaedco", "eedc", "bedc", "yedc", "aple"}:
                    continue
                discos.append({"code": code, "id": key, "name": key.upper()})

        dedup: dict[str, dict] = {}
        for item in discos:
            dedup[item["id"]] = item
        return list(dedup.values())

    def verify_electricity_customer(self, disco: str, meter_number: str, meter_type: str) -> dict:
        data = self._request(
            "APIVerifyElectricityV1.asp",
            {
                "ElectricCompany": self._disco_code(disco),
                "MeterNo": str(meter_number).strip(),
                "MeterType": self._meter_code(meter_type),
            },
        )
        customer_name = str(data.get("customer_name") or data.get("CustomerName") or "").strip()
        if customer_name and customer_name.upper() not in {"INVALID_METERNO", "INVALID"}:
            return {"ok": True, "customer_name": customer_name}
        return {"ok": False, "message": customer_name or "Unable to verify meter number."}

    def purchase_exam_pin(
        self,
        exam: str,
        quantity: int,
        phone_number: str | None = None,
        exam_type: str | None = None,
    ) -> ProviderResult:
        exam_key = _normalize_exam_key(exam)
        endpoint = {
            "waec": "APIWAECV1.asp",
            "jamb": "APIJAMBV1.asp",
        }.get(exam_key)
        if not endpoint:
            return ProviderResult(False, message=f"Unsupported exam type on ClubKonnect: {exam}")

        request_id = _clubkonnect_request_id("EXAM")
        params = {
            "ExamType": str(exam_type or exam_key).strip(),
            "RequestID": request_id,
            "CallBackURL": self._callback_url(),
        }
        if phone_number:
            params["PhoneNo"] = str(phone_number).strip()

        data = self._request(endpoint, params)
        result = self._settle_pending(self._parse_result(data, action=f"exam:{exam_key}"), f"exam:{exam_key}", request_id=request_id)
        pins = self._extract_exam_pins(data)
        if pins:
            result.meta = {**(result.meta or {}), "pins": pins}
        return result

    def fetch_exam_packages(self, exam: str) -> list[dict]:
        exam_key = _normalize_exam_key(exam)
        endpoint = {
            "waec": "APIWAECPackagesV2.asp",
            "jamb": "APIJAMBPackagesV2.asp",
        }.get(exam_key)
        if not endpoint:
            return []
        data = self._request(endpoint, {})
        rows = data.get("data") or data.get("Data") or data.get("EXAM_TYPE") or data.get("exam_type") or data
        flattened: list[dict] = []
        if isinstance(rows, list):
            flattened = [row for row in rows if isinstance(row, dict)]
        elif isinstance(rows, dict):
            for value in rows.values():
                if isinstance(value, list):
                    flattened.extend([item for item in value if isinstance(item, dict)])
                elif isinstance(value, dict):
                    flattened.append(value)

        packages: list[dict] = []
        for row in flattened:
            code = str(
                row.get("ExamType")
                or row.get("exam_type")
                or row.get("PRODUCT_CODE")
                or row.get("product_code")
                or row.get("ID")
                or row.get("code")
                or row.get("Package")
                or ""
            ).strip()
            name = str(
                row.get("Description")
                or row.get("description")
                or row.get("PRODUCT_DESCRIPTION")
                or row.get("product_description")
                or row.get("Name")
                or row.get("name")
                or row.get("ExamName")
                or code
            ).strip()
            amount_raw = (
                row.get("Amount")
                or row.get("amount")
                or row.get("PRODUCT_AMOUNT")
                or row.get("product_amount")
                or row.get("Price")
                or row.get("price")
            )
            try:
                amount = float(str(amount_raw).replace(",", "").strip()) if amount_raw not in (None, "") else None
            except Exception:
                amount = None
            if not code:
                continue
            packages.append({"code": code, "name": name or code, "amount": amount, "exam": exam_key})
        dedup: dict[str, dict] = {}
        for item in packages:
            dedup[item["code"]] = item
        return list(dedup.values())

    def fetch_data_variations(self, network: str) -> list[dict]:
        target = self._network_code(network)
        for endpoint in ("APIDatabundlePlansV2.asp", "APIDatabundlePlansV1.asp"):
            try:
                data = self._request(endpoint, {"MobileNetwork": target})
            except Exception as exc:
                logger.warning("ClubKonnect fetch_data_variations failed endpoint=%s network=%s error=%s", endpoint, network, exc)
                continue
            rows = self._flatten_data_plan_rows(data)
            if not rows:
                continue

            matched = []
            unknown_network_rows = []
            for row in rows:
                row_network = self._normalize_network_code(
                    row.get("MobileNetwork")
                    or row.get("mobile_network")
                    or row.get("Network")
                    or row.get("network")
                    or row.get("NetworkID")
                    or row.get("network_id")
                    or row.get("IDNetwork")
                    or row.get("IDNETWORK")
                )
                if row_network == target:
                    matched.append(row)
                elif not row_network:
                    unknown_network_rows.append(row)
            if matched:
                return matched
            # Some payloads omit explicit network per row; if all rows are unknown,
            # assume endpoint-side filtering already applied.
            if unknown_network_rows and len(unknown_network_rows) == len(rows):
                return unknown_network_rows
        return []

    def purchase_data(
        self,
        network: str,
        phone_number: str,
        plan_code: str,
        amount: float | None = None,
        request_id: str | None = None,
    ) -> ProviderResult:
        req_id = request_id or _clubkonnect_request_id("DATA")
        data = self._request(
            "APIDatabundleV1.asp",
            {
                "MobileNetwork": self._network_code(network),
                "DataPlan": str(plan_code),
                "MobileNumber": str(phone_number),
                "RequestID": req_id,
                "CallBackURL": self._callback_url(),
            },
        )
        return self._settle_pending(self._parse_result(data, action="data"), "data", request_id=req_id)


def get_bills_provider():
    choice = str(settings.bills_provider or "auto").strip().lower()

    has_vtpass = bool(settings.vtpass_enabled and settings.vtpass_api_key and settings.vtpass_secret_key)
    has_clubkonnect = bool((settings.nello_user_id or settings.clubkonnect_user_id) and (settings.nello_api_key or settings.clubkonnect_api_key))
    clubkonnect_enabled = bool(settings.clubkonnect_enabled)

    if choice == "mock":
        return MockBillsProvider()
    if choice == "clubkonnect":
        if has_clubkonnect:
            return ClubKonnectBillsProvider()
        logger.warning("BILLS_PROVIDER=clubkonnect but CLUBKONNECT_USER_ID/API_KEY missing; falling back to mock.")
        return MockBillsProvider()
    if choice == "vtpass":
        if has_vtpass:
            return VTPassBillsProvider()
        logger.warning("BILLS_PROVIDER=vtpass but VTPASS credentials missing; falling back to mock.")
        return MockBillsProvider()

    # auto mode
    if clubkonnect_enabled and has_clubkonnect:
        return ClubKonnectBillsProvider()
    if has_vtpass:
        return VTPassBillsProvider()
    if has_clubkonnect:
        return ClubKonnectBillsProvider()
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

    def purchase_exam_pin(
        self,
        exam: str,
        quantity: int,
        phone_number: str | None = None,
        exam_type: str | None = None,
    ) -> ProviderResult:
        pins = []
        for _ in range(int(quantity or 1)):
            pins.append(f"{secrets.randbelow(10**12):012d}")
        return ProviderResult(
            True,
            external_reference=self._ref("EXAM"),
            meta={"exam": exam, "exam_type": exam_type, "pins": pins, "phone_number": phone_number},
        )

    def fetch_exam_packages(self, exam: str) -> list[dict]:
        key = _normalize_exam_key(exam)
        defaults = {
            "waec": [{"code": "waecdirect", "name": "WAEC Result Checker PIN", "amount": 2000.0, "exam": "waec"}],
            "jamb": [
                {"code": "de", "name": "Direct Entry (DE)", "amount": 2000.0, "exam": "jamb"},
                {"code": "utme-mock", "name": "UTME PIN (with mock)", "amount": 2000.0, "exam": "jamb"},
                {"code": "utme-no-mock", "name": "UTME PIN (without mock)", "amount": 2000.0, "exam": "jamb"},
            ],
        }
        return defaults.get(key, [])

    def fetch_cable_packages(self, provider: str) -> list[dict]:
        return []

    def verify_cable_customer(self, provider: str, smartcard_number: str) -> dict:
        if str(smartcard_number).strip().startswith("0000"):
            return {"ok": False, "message": "Invalid smartcard number."}
        return {"ok": True, "customer_name": "Verified Customer"}

    def fetch_electricity_discos(self) -> list[dict]:
        return [
            {"code": "01", "id": "ekedc", "name": "EKEDC"},
            {"code": "02", "id": "ikedc", "name": "IKEDC"},
            {"code": "03", "id": "aedc", "name": "AEDC"},
            {"code": "04", "id": "kedco", "name": "KEDCO"},
            {"code": "05", "id": "phed", "name": "PHED"},
            {"code": "06", "id": "jos", "name": "JED"},
            {"code": "07", "id": "ibedc", "name": "IBEDC"},
            {"code": "08", "id": "kaedco", "name": "KAEDCO"},
            {"code": "09", "id": "eedc", "name": "EEDC"},
            {"code": "10", "id": "bedc", "name": "BEDC"},
            {"code": "11", "id": "yedc", "name": "YEDC"},
            {"code": "12", "id": "aple", "name": "APLE"},
        ]

    def verify_electricity_customer(self, disco: str, meter_number: str, meter_type: str) -> dict:
        if str(meter_number).strip().startswith("0000"):
            return {"ok": False, "message": "Invalid meter number."}
        return {"ok": True, "customer_name": "Verified Meter Customer"}
