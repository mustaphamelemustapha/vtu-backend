import logging
import re
import time
import httpx
from typing import Dict, Any, List, Optional
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.integration import IntegrationProvider

settings = get_settings()
logger = logging.getLogger(__name__)

# Persistent connection pool with Keep-Alive to eliminate TLS/TCP handshake latency
_TELECOM_ABODE_CLIENT = httpx.Client(
    timeout=httpx.Timeout(connect=8.0, read=25.0, write=10.0, pool=10.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=60.0),
)

_TOKEN_RE = re.compile(r"\btoken\b\s*[:=]\s*([0-9\s-]+)", re.IGNORECASE)
_GENERIC_TOKEN_RE = re.compile(r"\b(?:\d[- ]*){20}\b")

_DEFAULT_CABLE_PACKAGES: dict[str, list[dict]] = {
    "gotv": [
        {"code": "1", "name": "GOtv Max", "amount": 8500.0, "provider": "gotv"},
        {"code": "2", "name": "GOtv Jolli", "amount": 5800.0, "provider": "gotv"},
        {"code": "3", "name": "GOtv Jinja", "amount": 3900.0, "provider": "gotv"},
        {"code": "4", "name": "GOtv Smallie", "amount": 1900.0, "provider": "gotv"},
        {"code": "5", "name": "GOtv Supa", "amount": 11400.0, "provider": "gotv"},
        {"code": "6", "name": "GOtv Supa Plus", "amount": 15700.0, "provider": "gotv"},
    ],
    "dstv": [
        {"code": "dstv-padi", "name": "DStv Padi", "amount": 4400.0, "provider": "dstv"},
        {"code": "dstv-yanga", "name": "DStv Yanga", "amount": 6000.0, "provider": "dstv"},
        {"code": "dstv-confam", "name": "DStv Confam", "amount": 11000.0, "provider": "dstv"},
        {"code": "dstv-compact", "name": "DStv Compact", "amount": 19000.0, "provider": "dstv"},
        {"code": "dstv-compact-plus", "name": "DStv Compact Plus", "amount": 30000.0, "provider": "dstv"},
        {"code": "dstv-premium", "name": "DStv Premium", "amount": 44000.0, "provider": "dstv"},
    ],
    "startimes": [
        {"code": "nova", "name": "Nova Bouquet", "amount": 1700.0, "provider": "startimes"},
        {"code": "basic", "name": "Basic Bouquet", "amount": 3300.0, "provider": "startimes"},
        {"code": "smart", "name": "Smart Bouquet", "amount": 4500.0, "provider": "startimes"},
        {"code": "classic", "name": "Classic Bouquet", "amount": 5000.0, "provider": "startimes"},
        {"code": "super", "name": "Super Bouquet", "amount": 9000.0, "provider": "startimes"},
    ],
    "showmax": [
        {"code": "showmax-mobile", "name": "Showmax Mobile", "amount": 1600.0, "provider": "showmax"},
        {"code": "showmax-standard", "name": "Showmax Standard", "amount": 3200.0, "provider": "showmax"},
        {"code": "showmax-pro", "name": "Showmax Pro", "amount": 4500.0, "provider": "showmax"},
    ],
}


class TelecomAbodeProvider:
    """
    Telecom Abode API Integration Provider.
    Supports:
      - Airtime (VTU)
      - Data
      - Electricity (DisCos catalog, meter verification, prepaid/postpaid purchase)
      - Cable TV (DStv, GOtv, StarTimes plans, smartcard verification, subscription purchase)
      - Exam PINs (WAEC, NECO, NABTEB, JAMB catalog, PIN purchase)
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None, db=None):
        db_prov = None
        try:
            _db = db or SessionLocal()
            try:
                db_prov = _db.query(IntegrationProvider).filter(IntegrationProvider.identifier == "telecom_abode").first()
            finally:
                if db is None:
                    _db.close()
        except Exception as e:
            logger.warning(f"Could not load telecom_abode config from DB: {e}")

        if db_prov and db_prov.is_active:
            self.base_url = str(base_url or db_prov.base_url or settings.telecom_abode_base_url).rstrip("/")
            self.api_key = api_key if api_key else (db_prov.api_key or settings.telecom_abode_api_key)
        else:
            self.base_url = str(base_url or settings.telecom_abode_base_url).rstrip("/")
            self.api_key = api_key if api_key else settings.telecom_abode_api_key

        self.timeout = float(getattr(settings, "telecom_abode_timeout_seconds", 25))
        self._cached_discos: list[dict] = []
        self._cached_discos_ts: float = 0.0
        self._cached_exams: list[dict] = []
        self._cached_exams_ts: float = 0.0

    def _get_headers(self) -> dict:
        token = (self.api_key or "").strip()
        return {
            "Authorization": f"Token {token}" if token else "Token none",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _json_or_none(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return None

    def _map_network_to_id(self, network: str) -> int:
        mapping = {
            "mtn": 1,
            "airtel": 2,
            "glo": 3,
            "9mobile": 4,
            "etisalat": 4
        }
        return mapping.get(str(network).strip().lower(), 1)

    # ==========================================
    # AIRTIME & DATA
    # ==========================================

    def purchase_airtime(self, network: str, phone: str, amount: float, request_id: str, type_val: str = "VTU") -> Dict[str, Any]:
        if not self.api_key:
            return {"status": "failed", "error": "Telecom Abode API key is not configured"}

        url = f"{self.base_url}/airtime"
        payload = {
            "network": self._map_network_to_id(network),
            "phone": str(phone),
            "amount": str(int(amount)),
            "request-id": str(request_id),
            "type": type_val,
        }

        try:
            response = _TELECOM_ABODE_CLIENT.post(url, json=payload, headers=self._get_headers())
            logger.info("TelecomAbode POST %s network=%s phone=%s status=%d", url, network, phone, response.status_code)
            res_data = self._json_or_none(response) or {}
            status_value = str(res_data.get("status") or "").lower()
            inner_status = str(res_data.get("Status") or "").lower()
            message = str(res_data.get("message") or "")

            if status_value == "success" or inner_status == "successful":
                return {
                    "status": "success",
                    "provider_reference": str(res_data.get("request-id") or request_id),
                    "message": message,
                    "error": message,
                    "meta": res_data,
                }
            elif status_value == "pending" or inner_status == "pending":
                return {
                    "status": "pending",
                    "provider_reference": str(res_data.get("request-id") or request_id),
                    "message": message,
                    "error": message,
                    "meta": res_data,
                }

            return {
                "status": "failed",
                "provider_reference": str(res_data.get("request-id") or request_id),
                "message": message or "Airtime purchase failed",
                "error": message or "Airtime purchase failed",
                "meta": res_data,
            }
        except Exception as exc:
            logger.error("TelecomAbode airtime exception: %s", exc)
            return {"status": "pending", "error": f"Provider error: {str(exc)}", "message": str(exc)}

    def purchase_data(self, network: str, phone: str, plan_id: str, request_id: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"status": "failed", "error": "Telecom Abode API key is not configured"}

        url = f"{self.base_url}/data"
        payload = {
            "network": self._map_network_to_id(network),
            "phone": str(phone),
            "plan": str(plan_id),
            "request-id": str(request_id),
        }

        try:
            response = _TELECOM_ABODE_CLIENT.post(url, json=payload, headers=self._get_headers())
            logger.info("TelecomAbode POST %s network=%s phone=%s status=%d", url, network, phone, response.status_code)
            res_data = self._json_or_none(response) or {}
            status_value = str(res_data.get("status") or "").lower()
            inner_status = str(res_data.get("Status") or "").lower()
            message = str(res_data.get("message") or "")

            if status_value == "success" or inner_status == "successful":
                return {
                    "status": "success",
                    "provider_reference": str(res_data.get("request-id") or request_id),
                    "message": message,
                    "error": message,
                    "meta": res_data,
                }
            elif status_value == "pending" or inner_status == "pending":
                return {
                    "status": "pending",
                    "provider_reference": str(res_data.get("request-id") or request_id),
                    "message": message,
                    "error": message,
                    "meta": res_data,
                }

            return {
                "status": "failed",
                "provider_reference": str(res_data.get("request-id") or request_id),
                "message": message or "Data purchase failed",
                "error": message or "Data purchase failed",
                "meta": res_data,
            }
        except Exception as exc:
            logger.error("TelecomAbode data exception: %s", exc)
            return {"status": "pending", "error": f"Provider error: {str(exc)}", "message": str(exc)}

    # ==========================================
    # ELECTRICITY
    # ==========================================

    def fetch_electricity_discos(self) -> list[dict]:
        """
        GET /api/get-bill
        Returns DisCo list: [{id, name, abb, apidiscount, ...}]
        """
        now = time.time()
        if self._cached_discos and (now - self._cached_discos_ts < 3600):
            return self._cached_discos

        url = f"{self.base_url}/get-bill"
        try:
            response = _TELECOM_ABODE_CLIENT.get(url, headers=self._get_headers())
            data = self._json_or_none(response)
            if isinstance(data, list):
                self._cached_discos = data
                self._cached_discos_ts = now
                return data
            elif isinstance(data, dict):
                # Check if wrapped in data or list
                items = data.get("data") or data.get("discos") or data.get("bills") or []
                if isinstance(items, list) and items:
                    self._cached_discos = items
                    self._cached_discos_ts = now
                    return items
        except Exception as exc:
            self._cached_discos_ts = now
            logger.warning("TelecomAbode fetch_electricity_discos failed: %s", exc)

        return self._cached_discos or [
            {"id": "1", "name": "Ikeja Electric", "abb": "IE", "apidiscount": "0.50"},
            {"id": "2", "name": "Eko Electric", "abb": "EKEDC", "apidiscount": "0.50"},
            {"id": "8", "name": "Abuja Electric", "abb": "AEDC", "apidiscount": "0.60"},
            {"id": "6", "name": "Ibadan Electric", "abb": "IBEDC", "apidiscount": "0.55"},
            {"id": "12", "name": "Aba Electric", "abb": "ABEDC", "apidiscount": "1.00"},
        ]

    def _resolve_disco_id(self, disco: str | int, disco_code: str | None = None) -> int:
        """
        Resolves a disco name, slug, or standard VTU code to Telecom Abode's integer provider ID.
        Known DisCos from docs:
          - 1: Ikeja Electric (IE)
          - 2: Eko Electric (EKEDC)
          - 8: Abuja Electric (AEDC)
          - 6: Ibadan Electric (IBEDC)
          - 12: Aba Electric (ABEDC)
        """
        raw_str = str(disco or disco_code or "").strip()
        if raw_str.isdigit():
            val = int(raw_str)
            # If standard 2-digit VTU code (e.g. 01, 02), map to Telecom Abode ID
            code_map = {
                1: 2,   # 01 EKEDC -> 2
                2: 1,   # 02 IKEDC -> 1
                3: 8,   # 03 AEDC -> 8
                7: 6,   # 07 IBEDC -> 6
                12: 12, # 12 ABA -> 12
            }
            if val in code_map:
                return code_map[val]
            return val

        candidates = [str(disco or "").strip(), str(disco_code or "").strip()]
        clean_list = [c.lower().replace("-", "").replace("_", "").replace(" ", "") for c in candidates if c]

        # 1. Check live fetched DisCos
        try:
            discos = self.fetch_electricity_discos()
            for d in (discos or []):
                if not isinstance(d, dict):
                    continue
                d_id = d.get("id")
                d_name = str(d.get("name") or "").lower().replace("-", "").replace("_", "").replace(" ", "")
                d_abb = str(d.get("abb") or "").lower().replace("-", "").replace("_", "").replace(" ", "")
                for clean in clean_list:
                    if clean and (clean in d_name or clean in d_abb or d_abb in clean):
                        try:
                            return int(d_id)
                        except Exception:
                            pass
        except Exception:
            pass

        # 2. Static fallbacks based on official DisCo abbreviations
        static_map = {
            "ikeja": 1,
            "ikedc": 1,
            "ie": 1,
            "eko": 2,
            "ekedc": 2,
            "abuja": 8,
            "aedc": 8,
            "ibadan": 6,
            "ibedc": 6,
            "aba": 12,
            "abedc": 12,
            "aple": 12,
            "kano": 4,
            "kedco": 4,
            "port": 5,
            "portharcourt": 5,
            "phed": 5,
            "jos": 7,
            "jed": 7,
            "jedc": 7,
            "kaduna": 9,
            "kaedco": 9,
            "enugu": 10,
            "eedc": 10,
            "benin": 11,
            "bedc": 11,
            "yola": 13,
            "yedc": 13,
        }
        for clean in clean_list:
            for k, v in static_map.items():
                if k in clean:
                    return v

        return 1

    def verify_electricity_meter(self, disco: str | int, meter_number: str, meter_type: str = "prepaid") -> Dict[str, Any]:
        """
        POST /api/bill/bill-validation
        Body: {"disco": <int>, "meter_number": <str>, "meter_type": "prepaid"|"postpaid"}
        """
        if not self.api_key:
            return {"ok": False, "message": "Telecom Abode API key is not configured"}

        disco_id = self._resolve_disco_id(disco)
        clean_type = "postpaid" if "post" in str(meter_type).lower() else "prepaid"
        clean_meter = str(meter_number).strip()

        url = f"{self.base_url}/bill/bill-validation"
        payload = {
            "disco": disco_id,
            "meter_number": clean_meter,
            "meter_type": clean_type,
        }

        try:
            response = _TELECOM_ABODE_CLIENT.post(url, json=payload, headers=self._get_headers())
            logger.info("TelecomAbode verify_meter POST %s disco=%d meter=%s status=%d",
                        url, disco_id, clean_meter, response.status_code)
            res_data = self._json_or_none(response) or {}

            status_val = str(res_data.get("status") or "").lower()
            customer_name = str(res_data.get("name") or res_data.get("customer_name") or "").strip()
            customer_address = str(res_data.get("customer_address") or res_data.get("address") or "").strip()
            message = str(res_data.get("message") or "")

            if status_val == "success" or customer_name:
                return {
                    "ok": True,
                    "customer_name": customer_name,
                    "customer_address": customer_address,
                    "message": message or "Meter verified successfully",
                    "raw": res_data,
                }

            return {
                "ok": False,
                "customer_name": "",
                "message": message or res_data.get("error") or "Invalid meter number or verification failed",
                "raw": res_data,
            }
        except Exception as exc:
            logger.error("TelecomAbode verify_meter exception: %s", exc)
            return {"ok": False, "message": f"Meter verification error: {str(exc)}"}

    def purchase_electricity(self, disco: str | int, meter_number: str, meter_type: str, amount: float, request_id: str) -> Dict[str, Any]:
        """
        POST /api/bill
        Body: {"disco": <int>, "meter_number": <str>, "meter_type": "prepaid"|"postpaid", "amount": <str>, "request-id": <str>}
        """
        if not self.api_key:
            return {"status": "failed", "error": "Telecom Abode API key is not configured", "message": "API key not configured"}

        disco_id = self._resolve_disco_id(disco)
        clean_type = "postpaid" if "post" in str(meter_type).lower() else "prepaid"
        clean_meter = str(meter_number).strip()
        clean_amount = str(int(amount))

        url = f"{self.base_url}/bill"
        payload = {
            "disco": disco_id,
            "provider_id": disco_id,
            "meter_number": clean_meter,
            "meter_type": clean_type,
            "amount": clean_amount,
            "request-id": str(request_id),
        }

        try:
            response = _TELECOM_ABODE_CLIENT.post(url, json=payload, headers=self._get_headers())
            logger.info("TelecomAbode purchase_electricity POST %s disco=%d meter=%s amount=%s status=%d",
                        url, disco_id, clean_meter, clean_amount, response.status_code)
            res_data = self._json_or_none(response) or {}

            status_value = str(res_data.get("status") or "").lower()
            inner_status = str(res_data.get("Status") or "").lower()
            message = str(res_data.get("message") or "")
            ref = str(res_data.get("request-id") or request_id)

            # Token extraction:
            # 1. Direct 'token' field in response
            token = str(res_data.get("token") or "").strip()
            # 2. Extract from response/api_response string (e.g. "IKEDC Payment Successful for 1234567890. Token: 1234 5678 9012 3456 7890")
            if not token:
                search_text = f"{res_data.get('response', '')} {res_data.get('api_response', '')} {message}"
                match = _TOKEN_RE.search(search_text) or _GENERIC_TOKEN_RE.search(search_text)
                if match:
                    token = match.group(1 if match.lastindex else 0).strip()

            meta = {
                "token": token,
                "meter_number": clean_meter,
                "meter_type": clean_type,
                "disco": str(disco),
                "disco_id": disco_id,
                "units": str(res_data.get("units") or ""),
                "amount": clean_amount,
                "raw": res_data,
            }

            if status_value == "success" or inner_status == "successful":
                return {
                    "status": "success",
                    "provider_reference": ref,
                    "token": token,
                    "message": message or "Electricity Payment Successful.",
                    "error": message,
                    "meta": meta,
                }
            elif status_value == "pending" or inner_status == "pending":
                return {
                    "status": "pending",
                    "provider_reference": ref,
                    "token": token,
                    "message": message or "Electricity purchase pending confirmation",
                    "error": message,
                    "meta": meta,
                }

            return {
                "status": "failed",
                "provider_reference": ref,
                "token": token,
                "message": message or "Electricity payment failed",
                "error": message or "Electricity payment failed",
                "meta": meta,
            }
        except Exception as exc:
            logger.error("TelecomAbode purchase_electricity exception: %s", exc)
            return {
                "status": "pending",
                "error": f"Provider error: {str(exc)}",
                "message": str(exc),
                "meta": {"token": "", "error": str(exc)},
            }

    # ==========================================
    # CABLE TV
    # ==========================================

    def _resolve_cable_provider_info(self, provider: str) -> tuple[int, str]:
        """
        Resolves provider to (cable_id: int, query_name: str).
        Telecom Abode supports:
          - DStv: ID 1, query "Dstv"
          - GOtv: ID 2, query "Gotv"
          - StarTimes: ID 3, query "Startimes"
          - Showmax: ID 4, query "Showmax"
        """
        clean = str(provider or "").strip().lower().replace("-", "").replace("_", "")
        if "dstv" in clean:
            return 1, "Dstv"
        if "gotv" in clean:
            return 2, "Gotv"
        if "star" in clean:
            return 3, "Startimes"
        if "show" in clean:
            return 4, "Showmax"

        if clean == "1":
            return 1, "Dstv"
        if clean == "2":
            return 2, "Gotv"
        if clean == "3":
            return 3, "Startimes"
        if clean == "4":
            return 4, "Showmax"

        return 2, "Gotv"

    def fetch_cable_packages(self, provider: str) -> list[dict]:
        """
        GET /api/get-cable-providers?cable=<CableName>
        Returns: [{"id": "1", "name": "GOtv Max", "price": "8500"}, ...]
        Normalized to: [{"code": "1", "name": "GOtv Max", "amount": 8500.0, "provider": "gotv"}]
        """
        _, query_name = self._resolve_cable_provider_info(provider)
        url = f"{self.base_url}/get-cable-providers?cable={query_name}"

        try:
            response = _TELECOM_ABODE_CLIENT.get(url, headers=self._get_headers())
            data = self._json_or_none(response)
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("data") or data.get("plans") or data.get("packages") or []

            normalized = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("id") or item.get("code") or item.get("plan_id") or "").strip()
                name = str(item.get("name") or item.get("plan_name") or code).strip()
                price_raw = item.get("price") or item.get("amount")
                try:
                    price = float(price_raw) if price_raw not in (None, "") else None
                except Exception:
                    price = None
                if code:
                    normalized.append({
                        "code": code,
                        "name": name or code,
                        "amount": price,
                        "provider": str(provider).strip().lower(),
                    })
            if normalized:
                return normalized
            prov_key = str(provider).strip().lower()
            return _DEFAULT_CABLE_PACKAGES.get(prov_key, [])
        except Exception as exc:
            logger.warning("TelecomAbode fetch_cable_packages failed for %s: %s", provider, exc)
            prov_key = str(provider).strip().lower()
            return _DEFAULT_CABLE_PACKAGES.get(prov_key, [])

    def verify_cable_smartcard(self, provider: str, smartcard: str) -> Dict[str, Any]:
        """
        POST /api/cable/cable-validation
        Body: {"cable": <int>, "iuc": <str>}
        """
        if not self.api_key:
            return {"ok": False, "message": "Telecom Abode API key is not configured"}

        cable_id, _ = self._resolve_cable_provider_info(provider)
        clean_iuc = str(smartcard).strip()

        url = f"{self.base_url}/cable/cable-validation"
        payload = {
            "cable": cable_id,
            "iuc": clean_iuc,
        }

        try:
            response = _TELECOM_ABODE_CLIENT.post(url, json=payload, headers=self._get_headers())
            logger.info("TelecomAbode verify_smartcard POST %s cable=%d iuc=%s status=%d",
                        url, cable_id, clean_iuc, response.status_code)
            res_data = self._json_or_none(response) or {}

            status_val = str(res_data.get("status") or "").lower()
            customer_name = str(res_data.get("name") or res_data.get("customer_name") or "").strip()
            customer_address = str(res_data.get("customer_address") or res_data.get("address") or "").strip()
            message = str(res_data.get("message") or "")

            if status_val == "success" or customer_name:
                return {
                    "ok": True,
                    "customer_name": customer_name,
                    "customer_address": customer_address,
                    "message": message or "Smartcard verified successfully",
                    "raw": res_data,
                }

            return {
                "ok": False,
                "customer_name": "",
                "message": message or res_data.get("error") or "Invalid smartcard/IUC number",
                "raw": res_data,
            }
        except Exception as exc:
            logger.error("TelecomAbode verify_smartcard exception: %s", exc)
            return {"ok": False, "message": f"Smartcard verification error: {str(exc)}"}

    def purchase_cable(self, provider: str, smartcard: str, plan_code: str, request_id: str) -> Dict[str, Any]:
        """
        POST /api/cable
        Body: {"cable": <int>, "provider_id": <int>, "iuc": <str>, "customer_id": <str>, "cable_plan": <str>, "plan": <str>, "request-id": <str>}
        """
        if not self.api_key:
            return {"status": "failed", "error": "Telecom Abode API key is not configured", "message": "API key not configured"}

        cable_id, _ = self._resolve_cable_provider_info(provider)
        clean_iuc = str(smartcard).strip()
        clean_plan = str(plan_code).strip()

        url = f"{self.base_url}/cable"
        payload = {
            "cable": cable_id,
            "provider_id": cable_id,
            "iuc": clean_iuc,
            "customer_id": clean_iuc,
            "cable_plan": clean_plan,
            "plan": clean_plan,
            "request-id": str(request_id),
        }

        try:
            response = _TELECOM_ABODE_CLIENT.post(url, json=payload, headers=self._get_headers())
            logger.info("TelecomAbode purchase_cable POST %s cable=%d iuc=%s plan=%s status=%d",
                        url, cable_id, clean_iuc, clean_plan, response.status_code)
            res_data = self._json_or_none(response) or {}

            status_value = str(res_data.get("status") or "").lower()
            inner_status = str(res_data.get("Status") or "").lower()
            message = str(res_data.get("message") or "")
            ref = str(res_data.get("request-id") or request_id)

            meta = {
                "provider": str(provider).strip().lower(),
                "cable_id": cable_id,
                "smartcard": clean_iuc,
                "package_code": clean_plan,
                "raw": res_data,
            }

            if status_value == "success" or inner_status == "successful":
                return {
                    "status": "success",
                    "provider_reference": ref,
                    "message": message or "Cable Subscription Successful.",
                    "error": message,
                    "meta": meta,
                }
            elif status_value == "pending" or inner_status == "pending":
                return {
                    "status": "pending",
                    "provider_reference": ref,
                    "message": message or "Cable subscription pending confirmation",
                    "error": message,
                    "meta": meta,
                }

            return {
                "status": "failed",
                "provider_reference": ref,
                "message": message or "Cable purchase failed",
                "error": message or "Cable purchase failed",
                "meta": meta,
            }
        except Exception as exc:
            logger.error("TelecomAbode purchase_cable exception: %s", exc)
            return {
                "status": "pending",
                "error": f"Provider error: {str(exc)}",
                "message": str(exc),
                "meta": {"error": str(exc)},
            }

    # ==========================================
    # EXAM PINS
    # ==========================================

    def fetch_exam_packages(self, exam: str | None = None) -> list[dict]:
        """
        GET /api/get-exam
        Returns catalog of supported exams:
        [{"id": "1", "name": "WAEC", "price": "3500"}, ...]
        Normalized to: [{"code": "1", "name": "WAEC Result Checker", "amount": 3500.0, "exam": "waec"}]
        """
        now = time.time()
        if not self._cached_exams or (now - self._cached_exams_ts > 3600):
            url = f"{self.base_url}/get-exam"
            try:
                response = _TELECOM_ABODE_CLIENT.get(url, headers=self._get_headers())
                data = self._json_or_none(response)
                items = []
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get("data") or data.get("exams") or []
                if items:
                    self._cached_exams = items
                    self._cached_exams_ts = now
            except Exception as exc:
                logger.warning("TelecomAbode fetch_exam_packages failed: %s", exc)

        items = self._cached_exams or [
            {"id": "1", "name": "WAEC", "price": "3500"},
            {"id": "2", "name": "NECO", "price": "2250"},
            {"id": "3", "name": "NABTEB", "price": "1000"},
            {"id": "4", "name": "JAMB", "price": "1050"},
        ]

        clean_exam = str(exam or "").strip().lower()
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                continue
            exam_name = str(item.get("name") or "").strip()
            item_exam_key = exam_name.lower()
            if clean_exam and clean_exam not in item_exam_key and item_exam_key not in clean_exam:
                continue
            code = str(item.get("id") or item.get("code") or "").strip()
            price_raw = item.get("price") or item.get("amount")
            try:
                price = float(price_raw) if price_raw not in (None, "") else None
            except Exception:
                price = None
            if code:
                normalized.append({
                    "code": code,
                    "name": f"{exam_name.upper()} Result Checker",
                    "amount": price,
                    "exam": item_exam_key,
                })
        return normalized

    def _resolve_exam_id(self, exam: str | int) -> int:
        """
        Resolves exam name or ID to Telecom Abode's exam integer ID.
          1: WAEC
          2: NECO
          3: NABTEB
          4: JAMB
        """
        raw_str = str(exam or "").strip()
        if raw_str.isdigit():
            return int(raw_str)

        clean = raw_str.lower()
        if "waec" in clean:
            return 1
        if "neco" in clean:
            return 2
        if "nabteb" in clean:
            return 3
        if "jamb" in clean:
            return 4

        # Fallback to checking cached/live exams
        for e in self.fetch_exam_packages():
            if clean in str(e.get("name", "")).lower() or clean == str(e.get("exam", "")).lower():
                try:
                    return int(e["code"])
                except Exception:
                    pass
        return 1

    def purchase_exam_pin(self, exam: str | int, quantity: int = 1, request_id: str | None = None) -> Dict[str, Any]:
        """
        POST /api/exam
        Body: {"exam": <int>, "quantity": <int>, "request-id": <str>}
        """
        if not self.api_key:
            return {"status": "failed", "error": "Telecom Abode API key is not configured", "message": "API key not configured"}

        exam_id = self._resolve_exam_id(exam)
        clean_qty = max(1, int(quantity or 1))
        ref = str(request_id or f"EXAM_{int(time.time())}")

        url = f"{self.base_url}/exam"
        payload = {
            "exam": exam_id,
            "quantity": clean_qty,
            "request-id": ref,
        }

        try:
            response = _TELECOM_ABODE_CLIENT.post(url, json=payload, headers=self._get_headers())
            logger.info("TelecomAbode purchase_exam_pin POST %s exam=%d qty=%d status=%d",
                        url, exam_id, clean_qty, response.status_code)
            res_data = self._json_or_none(response) or {}

            status_value = str(res_data.get("status") or "").lower()
            inner_status = str(res_data.get("Status") or "").lower()
            message = str(res_data.get("message") or "")
            resp_ref = str(res_data.get("request-id") or ref)

            # Extract PINs
            pin_list: list[str] = []
            raw_pins = res_data.get("pins") or []
            if isinstance(raw_pins, list):
                for p in raw_pins:
                    if isinstance(p, dict) and p.get("pin"):
                        pin_str = str(p.get("pin")).strip()
                        serial_str = str(p.get("serial") or "").strip()
                        display = f"PIN: {pin_str} (Serial: {serial_str})" if serial_str else pin_str
                        pin_list.append(display)
                    elif isinstance(p, str) and p.strip():
                        pin_list.append(p.strip())

            # Fallback to single token field if pins array was empty
            token = str(res_data.get("token") or "").strip()
            if not pin_list and token:
                pin_list.append(token)

            meta = {
                "pins": pin_list,
                "raw_pins": raw_pins,
                "token": token or (pin_list[0] if pin_list else ""),
                "exam_id": exam_id,
                "quantity": clean_qty,
                "raw": res_data,
            }

            if status_value == "success" or inner_status == "successful":
                return {
                    "status": "success",
                    "provider_reference": resp_ref,
                    "pins": pin_list,
                    "token": token,
                    "message": message or "Exam PIN Purchase Successful.",
                    "error": message,
                    "meta": meta,
                }
            elif status_value == "pending" or inner_status == "pending":
                return {
                    "status": "pending",
                    "provider_reference": resp_ref,
                    "pins": pin_list,
                    "token": token,
                    "message": message or "Exam PIN purchase pending confirmation",
                    "error": message,
                    "meta": meta,
                }

            return {
                "status": "failed",
                "provider_reference": resp_ref,
                "pins": [],
                "token": "",
                "message": message or "Exam PIN purchase failed",
                "error": message or "Exam PIN purchase failed",
                "meta": meta,
            }
        except Exception as exc:
            logger.error("TelecomAbode purchase_exam_pin exception: %s", exc)
            return {
                "status": "pending",
                "error": f"Provider error: {str(exc)}",
                "message": str(exc),
                "pins": [],
                "meta": {"pins": [], "error": str(exc)},
            }
