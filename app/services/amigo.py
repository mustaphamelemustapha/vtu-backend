import time
import logging
import httpx
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
]


class AmigoClient:
    def __init__(self):
        self.base_url = str(settings.amigo_base_url)
        self.api_key = settings.amigo_api_key
        self.timeout = settings.amigo_timeout_seconds
        self.retry_count = settings.amigo_retry_count

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        last_exc = None
        for attempt in range(self.retry_count + 1):
            start = time.time()
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.request(method, url, json=payload, headers=self._headers())
                duration_ms = round((time.time() - start) * 1000, 2)
                logger.info("Amigo API %s %s status=%s duration=%sms", method, path, response.status_code, duration_ms)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_exc = exc
                if attempt < self.retry_count:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise last_exc

    def fetch_data_plans(self) -> dict:
        # Amigo does not provide a full catalog endpoint; use the published catalog.
        return {"data": PLAN_CATALOG}

    def purchase_data(self, payload: dict) -> dict:
        return self._request("POST", "/data/", payload)
