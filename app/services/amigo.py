import time
import logging
import json
import re
import httpx
from urllib.parse import urlparse, urlunparse
from app.core.config import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


PLAN_CATALOG = [
    # MTN (network_id = 1)
    {"network": "mtn", "network_id": 1, "plan_code": "5000", "plan_name": "MTN 500MB", "data_size": "500MB", "validity": "30d", "price": 299.00},
    {"network": "mtn", "network_id": 1, "plan_code": "1001", "plan_name": "MTN 1GB", "data_size": "1GB", "validity": "30d", "price": 429.00},
    {"network": "mtn", "network_id": 1, "plan_code": "6666", "plan_name": "MTN 2GB", "data_size": "2GB", "validity": "30d", "price": 849.00},
    {"network": "mtn", "network_id": 1, "plan_code": "3333", "plan_name": "MTN 3GB", "data_size": "3GB", "validity": "30d", "price": 1329.00},
    {"network": "mtn", "network_id": 1, "plan_code": "9999", "plan_name": "MTN 5GB", "data_size": "5GB", "validity": "30d", "price": 1799.00},
    {"network": "mtn", "network_id": 1, "plan_code": "7777", "plan_name": "MTN 7GB", "data_size": "7GB", "validity": "30d", "price": 2499.00},
    {"network": "mtn", "network_id": 1, "plan_code": "1110", "plan_name": "MTN 10GB", "data_size": "10GB", "validity": "30d", "price": 3899.00},
    {"network": "mtn", "network_id": 1, "plan_code": "1515", "plan_name": "MTN 15GB", "data_size": "15GB", "validity": "30d", "price": 5690.00},
    {"network": "mtn", "network_id": 1, "plan_code": "424", "plan_name": "MTN 20GB", "data_size": "20GB", "validity": "30d", "price": 7899.00},
    {"network": "mtn", "network_id": 1, "plan_code": "379", "plan_name": "MTN 36GB", "data_size": "36GB", "validity": "30d", "price": 11900.00},
    {"network": "mtn", "network_id": 1, "plan_code": "360", "plan_name": "MTN 75GB", "data_size": "75GB", "validity": "30d", "price": 18990.00},
    # Glo (network_id = 2)
    {"network": "glo", "network_id": 2, "plan_code": "218", "plan_name": "Glo 200MB", "data_size": "200MB", "validity": "30d", "price": 99.00},
    {"network": "glo", "network_id": 2, "plan_code": "217", "plan_name": "Glo 500MB", "data_size": "500MB", "validity": "30d", "price": 199.00},
    {"network": "glo", "network_id": 2, "plan_code": "206", "plan_name": "Glo 1GB", "data_size": "1GB", "validity": "30d", "price": 399.00},
    {"network": "glo", "network_id": 2, "plan_code": "195", "plan_name": "Glo 2GB", "data_size": "2GB", "validity": "30d", "price": 799.00},
    {"network": "glo", "network_id": 2, "plan_code": "196", "plan_name": "Glo 3GB", "data_size": "3GB", "validity": "30d", "price": 1199.00},
    {"network": "glo", "network_id": 2, "plan_code": "222", "plan_name": "Glo 5GB", "data_size": "5GB", "validity": "30d", "price": 1999.00},
    {"network": "glo", "network_id": 2, "plan_code": "512", "plan_name": "Glo 10GB", "data_size": "10GB", "validity": "30d", "price": 3990.00},
    # Airtel (network_id = 4)
    {"network": "airtel", "network_id": 4, "plan_code": "163", "plan_name": "Airtel 500MB", "data_size": "500MB", "validity": "7d", "price": 549.00},
    {"network": "airtel", "network_id": 4, "plan_code": "145", "plan_name": "Airtel 1GB", "data_size": "1GB", "validity": "30d", "price": 764.00},
    {"network": "airtel", "network_id": 4, "plan_code": "146", "plan_name": "Airtel 2GB", "data_size": "2GB", "validity": "30d", "price": 1430.00},
    {"network": "airtel", "network_id": 4, "plan_code": "532", "plan_name": "Airtel 3GB", "data_size": "3GB", "validity": "30d", "price": 1950.00},
    {"network": "airtel", "network_id": 4, "plan_code": "148", "plan_name": "Airtel 4GB", "data_size": "4GB", "validity": "30d", "price": 2619.00},
    {"network": "airtel", "network_id": 4, "plan_code": "150", "plan_name": "Airtel 10GB", "data_size": "10GB", "validity": "30d", "price": 3899.00},
    {"network": "airtel", "network_id": 4, "plan_code": "405", "plan_name": "Airtel 18GB", "data_size": "18GB", "validity": "30d", "price": 6450.00},
    {"network": "airtel", "network_id": 4, "plan_code": "404", "plan_name": "Airtel 25GB", "data_size": "25GB", "validity": "30d", "price": 8499.00},
]

NETWORK_ID_MAP = {
    "mtn": 1,
    "glo": 2,
    "airtel": 4,
    "9mobile": 9,
    "etisalat": 9,
}
NETWORK_ID_MAP.update(
    {
        item["network"].lower(): int(item["network_id"])
        for item in PLAN_CATALOG
        if item.get("network") and item.get("network_id") is not None
    }
)

_TEXT_SUCCESS_HINTS = (
    "success",
    "successful",
    "delivered",
    "completed",
    "gifted",
    "processed",
)
_TEXT_FAILURE_HINTS = (
    "failed",
    "unsuccessful",
    "declined",
    "rejected",
    "invalid",
    "insufficient",
    "error",
)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def split_plan_code(plan_code: str | None) -> tuple[str | None, str]:
    raw = str(plan_code or "").strip()
    if ":" in raw:
        network_hint, provider_code = raw.split(":", 1)
        network_hint = network_hint.strip().lower()
        provider_code = provider_code.strip()
        if network_hint in NETWORK_ID_MAP and provider_code:
            return network_hint, provider_code
    return None, raw


def canonical_plan_code(network: str, plan_code: str | None) -> str:
    network_key = str(network or "").strip().lower()
    _, provider_code = split_plan_code(plan_code)
    if network_key and provider_code:
        return f"{network_key}:{provider_code}"
    return provider_code


def resolve_network_id(network: str, plan_code: str | None = None) -> int | None:
    network_key = str(network or "").strip().lower()
    network_hint, plan_key = split_plan_code(plan_code)
    if network_hint:
        network_key = network_hint
    if plan_key:
        for item in PLAN_CATALOG:
            if str(item.get("plan_code")) == plan_key:
                network_id = item.get("network_id")
                if network_id is not None:
                    return int(network_id)
    return NETWORK_ID_MAP.get(network_key)


def normalize_plan_code(plan_code: str) -> int | str:
    _, raw = split_plan_code(plan_code)
    return int(raw) if raw.isdigit() else raw


def normalize_amigo_base_url(raw_url: str) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return "https://amigo.ng/api"

    if "api.amigo.com" in url:
        logger.warning("Legacy AMIGO_BASE_URL detected (%s). Switching to https://amigo.ng/api", url)
        return "https://amigo.ng/api"

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/")
    if host == "amigo.ng" and not path:
        path = "/api"

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or host
    normalized = urlunparse((scheme, netloc, path, "", "", ""))
    return normalized.rstrip("/")


def _format_data_size(value) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number >= 1:
        if number.is_integer():
            return f"{int(number)}GB"
        return f"{number:g}GB"
    mb = int(round(number * 1024))
    return f"{mb}MB"


def _format_validity(value) -> str:
    if value in (None, ""):
        return "30d"
    try:
        number = int(value)
        return f"{number}d"
    except (TypeError, ValueError):
        return str(value)


def parse_efficiency_plans(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    networks = ("mtn", "glo", "airtel", "9mobile")
    items = []
    for network in networks:
        rows = payload.get(network.upper()) or payload.get(network) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            plan_code = row.get("plan_id") or row.get("plan_code") or row.get("id")
            if plan_code in (None, ""):
                continue
            price = row.get("price")
            if price in (None, ""):
                continue
            try:
                price_value = float(price)
            except (TypeError, ValueError):
                continue
            data_size = _format_data_size(row.get("data_capacity") or row.get("data_size"))
            validity = _format_validity(row.get("validity"))
            plan_name = row.get("plan_name") or f"{network.upper()} {data_size}".strip()
            provider_plan_code = str(plan_code)
            items.append(
                {
                    "network": network,
                    "network_id": NETWORK_ID_MAP.get(network),
                    # Keep plan_code in provider-native format for compatibility;
                    # canonicalization is handled during DB upsert/purchase resolution.
                    "plan_code": provider_plan_code,
                    "provider_plan_code": provider_plan_code,
                    "plan_name": plan_name,
                    "data_size": data_size,
                    "validity": validity,
                    "price": price_value,
                }
            )
    return items


class AmigoApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, raw: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.raw = raw


class AmigoClient:
    def __init__(self):
        self.base_url = normalize_amigo_base_url(str(settings.amigo_base_url))
        self.api_key = settings.amigo_api_key
        self.timeout = settings.amigo_timeout_seconds
        self.retry_count = settings.amigo_retry_count
        self.data_purchase_path = str(getattr(settings, "amigo_data_purchase_path", "/data/") or "/data/").strip() or "/data/"
        self.plans_path = str(getattr(settings, "amigo_plans_path", "/plans/efficiency") or "/plans/efficiency").strip() or "/plans/efficiency"

    def _headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        }

    def _extract_error_message(self, response: httpx.Response) -> str:
        def _pick_message(value):
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                for item in value:
                    picked = _pick_message(item)
                    if picked:
                        return picked
                return None
            if isinstance(value, dict):
                for nested_key in (
                    "message",
                    "detail",
                    "error",
                    "errors",
                    "remark",
                    "status",
                    "reason",
                    "description",
                ):
                    if nested_key in value:
                        picked = _pick_message(value.get(nested_key))
                        if picked:
                            return picked
                return None
            return None

        try:
            data = response.json()
        except ValueError:
            data = {}
        if isinstance(data, (dict, list)):
            picked = _pick_message(data)
            if picked:
                return picked
        text = (response.text or "").strip()
        return text[:300] if text else f"HTTP {response.status_code}"

    def _parse_best_effort_payload(self, response: httpx.Response) -> dict:
        """
        Parse provider response defensively.
        Some upstream gateways intermittently return non-JSON wrappers while
        still including a useful success/failure payload in text.
        """
        try:
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except ValueError:
            pass

        raw_text = str(response.text or "").strip()
        if not raw_text:
            raise AmigoApiError("Amigo returned empty response.", status_code=response.status_code, raw=response.text)

        # Try exact JSON text first.
        try:
            payload = json.loads(raw_text)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

        # Try extracting first JSON object from wrapped/plain responses.
        match = _JSON_OBJECT_RE.search(raw_text)
        if match:
            candidate = match.group(0)
            try:
                payload = json.loads(candidate)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass

        lowered = raw_text.lower()
        if any(hint in lowered for hint in _TEXT_SUCCESS_HINTS):
            return {
                "success": True,
                "status": "delivered",
                "message": raw_text[:300],
            }
        if any(hint in lowered for hint in _TEXT_FAILURE_HINTS):
            return {
                "success": False,
                "status": "failed",
                "message": raw_text[:300],
            }

        # Provider sometimes returns 200 with unreadable wrapper text.
        # Treat this as pending/accepted instead of throwing an error so
        # upper layers do not auto-refund ambiguous outcomes.
        if 200 <= int(response.status_code or 0) < 300:
            return {
                "success": None,
                "status": "pending",
                "message": "Awaiting provider confirmation",
                "raw_text": raw_text[:500],
            }

        raise AmigoApiError(
            "Amigo returned invalid JSON response.",
            status_code=response.status_code,
            raw=response.text,
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        extra_headers: dict | None = None,
        retry_count_override: int | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        last_exc = None
        retry_count = self.retry_count if retry_count_override is None else max(0, int(retry_count_override))
        for attempt in range(retry_count + 1):
            start = time.time()
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    headers = self._headers()
                    if extra_headers:
                        headers.update(extra_headers)
                    response = client.request(method, url, json=payload, headers=headers)
                duration_ms = round((time.time() - start) * 1000, 2)
                logger.info("Amigo API %s %s status=%s duration=%sms", method, path, response.status_code, duration_ms)
                if response.status_code >= 500 and attempt < retry_count:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if response.status_code >= 400:
                    message = self._extract_error_message(response)
                    raise AmigoApiError(message, status_code=response.status_code, raw=response.text)
                return self._parse_best_effort_payload(response)
            except AmigoApiError as exc:
                last_exc = exc
                # Do not retry definitive client/path/auth errors.
                if exc.status_code is not None and exc.status_code < 500 and exc.status_code != 429:
                    raise last_exc
                if attempt < retry_count:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise last_exc
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_exc = AmigoApiError("Unable to reach data provider.", raw=str(exc))
                if attempt < retry_count:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise last_exc

    def fetch_data_plans(self) -> dict:
        if settings.amigo_test_mode:
            return {"data": PLAN_CATALOG}
        
        # Try primary plans path first
        paths_to_try = [self.plans_path]
        if self.plans_path != "/plans":
            paths_to_try.append("/plans")
            
        for path in paths_to_try:
            try:
                response = self._request("GET", path)
                parsed = parse_efficiency_plans(response)
                if parsed:
                    return {"data": parsed}
                logger.warning("Amigo plans response from %s parsed to 0 items.", path)
            except AmigoApiError as exc:
                if exc.status_code == 404:
                    logger.warning("Amigo plans path %s returned 404. Trying next...", path)
                    continue
                logger.warning("Amigo fetch error on %s: %s", path, exc)
            except Exception as exc:
                logger.warning("Failed to fetch Amigo plans from %s: %s", path, exc)
                
        return {"data": PLAN_CATALOG}

    def purchase_data(self, payload: dict, idempotency_key: str | None = None) -> dict:
        if settings.amigo_test_mode:
            phone = str(payload.get("mobile_number", "")).strip()
            # In explicit test mode we never hit the external provider.
            if phone.startswith("0000"):
                return {
                    "success": False,
                    "reference": f"AMG-TEST-FAIL-{int(time.time())}",
                    "message": "Test mode: simulated provider failure.",
                    "status": "failed",
                }
            return {
                "success": True,
                "reference": f"AMG-TEST-{int(time.time())}",
                "message": "Test mode: simulated delivery.",
                "status": "delivered",
            }
        extra_headers = {}
        if idempotency_key:
            extra_headers["Idempotency-Key"] = idempotency_key

        # Some Amigo deployments mount the same API behind different prefixes.
        # If we get a 404 from the configured path, try a small set of safe fallbacks.
        base_path = urlparse(self.base_url).path.rstrip("/").lower()
        candidates = [self.data_purchase_path, "/data/", "/v1/data/"]
        if not base_path.endswith("/api"):
            candidates.extend(["/api/data/", "/api/v1/data/"])

        # De-duplicate while preserving order.
        deduped = []
        seen = set()
        for path in candidates:
            path = (path or "").strip()
            if not path:
                continue
            if not path.startswith("/"):
                path = "/" + path
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)

        candidates = deduped
        tried = []
        last_exc: AmigoApiError | None = None
        for path in candidates:
            tried.append(path)
            try:
                # Purchase requests are low-retry with Idempotency-Key enabled.
                # This protects against transient network/provider edge failures
                # while still preventing duplicate debits.
                return self._request(
                    "POST",
                    path,
                    payload,
                    extra_headers=extra_headers,
                    retry_count_override=1,
                )
            except AmigoApiError as exc:
                last_exc = exc
                if exc.status_code == 404:
                    continue
                raise

        message = (
            "Amigo endpoint not found (404) on all known purchase paths. "
            "Confirm base URL/path from Amigo dashboard or enable AMIGO_TEST_MODE."
        )
        logger.warning("%s base_url=%s tried=%s", message, self.base_url, tried)
        raise AmigoApiError(message, status_code=404, raw=str(tried)) from last_exc
