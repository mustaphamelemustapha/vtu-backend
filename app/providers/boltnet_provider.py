import logging
import httpx
from typing import Dict, Any, List
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.integration import IntegrationProvider

settings = get_settings()
logger = logging.getLogger(__name__)

class BoltnetProviderError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, raw: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.raw = raw

class BoltnetProvider:
    def __init__(self, api_key: str | None = None, db=None):
        _db = db or SessionLocal()
        try:
            db_prov = _db.query(IntegrationProvider).filter(IntegrationProvider.identifier == "boltnet").first()
            if db_prov and db_prov.is_active:
                base_url = str(db_prov.base_url or settings.boltnet_base_url).rstrip("/")
                self.api_key = api_key if api_key else (db_prov.api_key or settings.boltnet_api_key)
            else:
                base_url = str(settings.boltnet_base_url).rstrip("/")
                self.api_key = api_key if api_key else settings.boltnet_api_key
        finally:
            if db is None:
                _db.close()
                
        if not base_url.endswith("/api"):
            base_url = f"{base_url}/api"
        self.base_url = base_url
        self.timeout = float(settings.boltnet_timeout_seconds)

    def _get_headers(self):
        token = self.api_key.strip() if self.api_key else ""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(method, url, headers=self._get_headers(), json=payload)
                
                if response.status_code >= 400:
                    try:
                        err_data = response.json()
                        msg = err_data.get("message") or err_data.get("detail") or response.text
                    except Exception:
                        msg = response.text
                    raise BoltnetProviderError(msg, status_code=response.status_code, raw=response.text)
                
                return response.json()
        except httpx.HTTPError as e:
            raise BoltnetProviderError(f"HTTP Error: {str(e)}")
        except Exception as e:
            raise BoltnetProviderError(f"Error: {str(e)}")

    def purchase_network_data(self, network: int, phone: str, plan_id: str, client_request_id: str) -> Dict[str, Any]:
        """
        Purchase Data via Boltnet
        """
        if not self.api_key:
            return {"status": "failed", "error": "Boltnet API key is not configured"}
            
        payload = {
            "network": network,
            "mobile_number": phone,
            "phone": phone,
            "phone_number": phone,
            "plan": plan_id,
            "Ported_number": True,
            "request_id": client_request_id
        }
        
        try:
            res = self._request("POST", "/data/", payload)
            if res.get("success") or str(res.get("Status")).lower() in {"delivered", "success", "successful"}:
                return {"status": "success", "provider_reference": str(res.get("id") or res.get("ident") or "")}
            elif str(res.get("Status")).lower() in {"pending", "processing"}:
                return {"status": "pending", "provider_reference": str(res.get("id") or res.get("ident") or "")}
            else:
                return {"status": "failed", "error": res.get("api_response") or res.get("description") or "Boltnet reported failure"}
        except BoltnetProviderError as e:
            err_msg = str(e)
            if any(h in err_msg.lower() for h in ["timeout", "timed out", "temporarily unavailable", "connection reset"]):
                logger.warning(f"Boltnet ambiguous error for data {client_request_id}: {err_msg}")
                return {"status": "pending", "error": err_msg}
            return {"status": "failed", "error": err_msg}

    def purchase_airtime(self, network: int, phone: str, amount: float, client_request_id: str) -> Dict[str, Any]:
        """
        Purchase Airtime via Boltnet
        """
        if not self.api_key:
            return {"status": "failed", "error": "Boltnet API key is not configured"}
            
        payload = {
            "network": network,
            "mobile_number": phone,
            "phone": phone,
            "phone_number": phone,
            "amount": int(amount),
            "airtime_type": "VTU",
            "Ported_number": True,
            "request_id": client_request_id
        }
        
        try:
            res = self._request("POST", "/topup/", payload)
            if res.get("success") or str(res.get("Status")).lower() in {"delivered", "success", "successful"}:
                return {"status": "success", "provider_reference": str(res.get("id") or res.get("ident") or ""), "meta": res}
            elif str(res.get("Status")).lower() in {"pending", "processing"}:
                return {"status": "pending", "provider_reference": str(res.get("id") or res.get("ident") or ""), "meta": res}
            else:
                return {"status": "failed", "error": res.get("api_response") or res.get("description") or "Boltnet reported failure", "meta": res}
        except BoltnetProviderError as e:
            err_msg = str(e)
            if any(h in err_msg.lower() for h in ["timeout", "timed out", "temporarily unavailable", "connection reset"]):
                logger.warning(f"Boltnet ambiguous error for airtime {client_request_id}: {err_msg}")
                return {"status": "pending", "error": err_msg, "meta": {"error": err_msg}}
            return {"status": "failed", "error": err_msg, "meta": {"error": err_msg}}
