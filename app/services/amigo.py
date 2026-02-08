import time
import logging
import httpx
from app.core.config import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


class AmigoClient:
    def __init__(self):
        self.base_url = str(settings.amigo_base_url)
        self.api_key = settings.amigo_api_key
        self.timeout = settings.amigo_timeout_seconds
        self.retry_count = settings.amigo_retry_count

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

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
        return self._request("GET", "/data/plans")

    def purchase_data(self, payload: dict) -> dict:
        return self._request("POST", "/data/purchase", payload)
