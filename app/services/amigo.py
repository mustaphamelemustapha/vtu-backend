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
    {"network": "mtn", "network_id": 1, "plan_code": "5000", "plan_name": "MTN 500MB", "data_size": "500MB", "validity": "30d", "price": 299.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "1001", "plan_name": "MTN 1GB", "data_size": "1GB", "validity": "30d", "price": 429.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "6666", "plan_name": "MTN 2GB", "data_size": "2GB", "validity": "30d", "price": 849.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "3333", "plan_name": "MTN 3GB", "data_size": "3GB", "validity": "30d", "price": 1329.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "9999", "plan_name": "MTN 5GB", "data_size": "5GB", "validity": "30d", "price": 1799.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "7777", "plan_name": "MTN 7GB", "data_size": "7GB", "validity": "30d", "price": 2499.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "1110", "plan_name": "MTN 10GB", "data_size": "10GB", "validity": "30d", "price": 3899.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "1515", "plan_name": "MTN 15GB", "data_size": "15GB", "validity": "30d", "price": 5690.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "424", "plan_name": "MTN 20GB", "data_size": "20GB", "validity": "30d", "price": 7899.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "379", "plan_name": "MTN 36GB", "data_size": "36GB", "validity": "30d", "price": 11900.00, "provider": "amigo"},
    {"network": "mtn", "network_id": 1, "plan_code": "360", "plan_name": "MTN 75GB", "data_size": "75GB", "validity": "30d", "price": 18990.00, "provider": "amigo"},
    # Glo (network_id = 3 as per Amigo docs)
    {"network": "glo", "network_id": 3, "plan_code": "218", "plan_name": "Glo 200MB", "data_size": "200MB", "validity": "30d", "price": 99.00, "provider": "amigo"},
    {"network": "glo", "network_id": 3, "plan_code": "217", "plan_name": "Glo 500MB", "data_size": "500MB", "validity": "30d", "price": 199.00, "provider": "amigo"},
    {"network": "glo", "network_id": 3, "plan_code": "206", "plan_name": "Glo 1GB", "data_size": "1GB", "validity": "30d", "price": 399.00, "provider": "amigo"},
    {"network": "glo", "network_id": 3, "plan_code": "195", "plan_name": "Glo 2GB", "data_size": "2GB", "validity": "30d", "price": 799.00, "provider": "amigo"},
    {"network": "glo", "network_id": 3, "plan_code": "196", "plan_name": "Glo 3GB", "data_size": "3GB", "validity": "30d", "price": 1199.00, "provider": "amigo"},
    {"network": "glo", "network_id": 3, "plan_code": "222", "plan_name": "Glo 5GB", "data_size": "5GB", "validity": "30d", "price": 1999.00, "provider": "amigo"},
    {"network": "glo", "network_id": 3, "plan_code": "512", "plan_name": "Glo 10GB", "data_size": "10GB", "validity": "30d", "price": 3990.00, "provider": "amigo"},
    # Airtel (network_id = 2)
    {"network": "airtel", "network_id": 2, "plan_code": "150", "plan_name": "Airtel 10GB", "data_size": "10GB", "validity": "30d", "price": 3100.00, "provider": "amigo"},
    {"network": "airtel", "network_id": 2, "plan_code": "151", "plan_name": "Airtel 20GB", "data_size": "20GB", "validity": "30d", "price": 5100.00, "provider": "amigo"},
]

NETWORK_ID_MAP = {
    "mtn": 1,
    "airtel": 2,
    "glo": 3,
    "9mobile": 4,
}

def resolve_network_id(network: str) -> int:
    return NETWORK_ID_MAP.get(str(network).lower(), 1)

def split_plan_code(plan_code: str | None) -> tuple[str | None, str]:
    raw = str(plan_code or "").strip()
    if ":" in raw:
        parts = raw.split(":")
        return parts[0].strip().lower(), parts[-1].strip()
    return None, raw

def canonical_plan_code(provider: str, network: str, plan_code: str | None) -> str:
    provider_key = str(provider or "").strip().lower()
    network_key = str(network or "").strip().lower()
    _, raw = split_plan_code(plan_code)
    
    if provider_key and network_key and raw:
        return f"{provider_key}:{network_key}:{raw}"
    if network_key and raw:
        return f"{network_key}:{raw}"
    return raw

def normalize_plan_code(plan_code: str) -> int | str:
    _, raw = split_plan_code(plan_code)
    return int(raw) if raw.isdigit() else raw

class AmigoApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, raw: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.raw = raw

class AmigoClient:
    def __init__(self):
        self.base_url = str(settings.amigo_base_url).rstrip("/")
        self.api_key = settings.amigo_api_key
        self.timeout = 30.0

    def _headers(self, idempotency_key: str | None = None) -> dict:
        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _request(self, method: str, path: str, payload: dict | None = None, idempotency_key: str | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(method, url, headers=self._headers(idempotency_key), json=payload)
                logger.info("Amigo API %s %s status=%d", method, path, response.status_code)
                
                if response.status_code >= 400:
                    try:
                        err_data = response.json()
                        msg = err_data.get("message") or err_data.get("detail") or response.text
                    except:
                        msg = response.text
                    raise AmigoApiError(msg, status_code=response.status_code, raw=response.text)
                
                return response.json()
        except httpx.HTTPError as e:
            raise AmigoApiError(f"HTTP Error: {str(e)}")
        except Exception as e:
            raise AmigoApiError(f"Error: {str(e)}")

    def fetch_data_plans(self) -> dict:
        # Default to efficiency plans
        try:
            res = self._request("GET", settings.amigo_plans_path)
            # Convert efficiency format to list of items
            items = []
            for nw, plans in res.items():
                if isinstance(plans, list):
                    for p in plans:
                        items.append({
                            "network": nw.lower(),
                            "plan_code": str(p.get("id") or p.get("plan_id")),
                            "plan_name": p.get("name") or p.get("plan_name"),
                            "data_size": p.get("size") or p.get("data_size"),
                            "validity": p.get("validity"),
                            "price": float(p.get("price") or 0),
                            "provider": "amigo"
                        })
            return {"data": items}
        except Exception:
            return {"data": PLAN_CATALOG}

    def purchase_data(self, payload: dict, idempotency_key: str | None = None) -> dict:
        # payload expected keys: network, mobile_number, plan, Ported_number (optional)
        # Amigo expects JSON body
        return self._request("POST", "/data/", payload, idempotency_key=idempotency_key)
