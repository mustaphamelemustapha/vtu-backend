import logging
import httpx
from typing import Dict, Any
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.integration import IntegrationProvider

settings = get_settings()
logger = logging.getLogger(__name__)

class TelecomAbodeProvider:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, db=None):
        _db = db or SessionLocal()
        try:
            db_prov = _db.query(IntegrationProvider).filter(IntegrationProvider.identifier == "telecom_abode").first()
            if db_prov and db_prov.is_active:
                self.base_url = str(base_url or db_prov.base_url or settings.telecom_abode_base_url).rstrip("/")
                self.api_key = api_key if api_key else (db_prov.api_key or settings.telecom_abode_api_key)
            else:
                self.base_url = str(base_url or settings.telecom_abode_base_url).rstrip("/")
                self.api_key = api_key if api_key else settings.telecom_abode_api_key
        finally:
            if db is None:
                _db.close()
        self.timeout = float(getattr(settings, "telecom_abode_timeout_seconds", 30))

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
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=self._get_headers())
                logger.info("TelecomAbode POST %s network=%s phone=%s status=%d", 
                            url, network, phone, response.status_code)
                
                res_data = self._json_or_none(response) or {}
                
                status_value = str(res_data.get("status") or "").lower()
                inner_status = str(res_data.get("Status") or "").lower()
                message = str(res_data.get("message") or "")
                
                if status_value == "success" or inner_status == "successful":
                    return {
                        "status": "success",
                        "provider_reference": str(res_data.get("request-id") or request_id),
                        "error": message
                    }
                elif status_value == "pending" or inner_status == "pending":
                    return {
                        "status": "pending",
                        "provider_reference": str(res_data.get("request-id") or request_id),
                        "error": message
                    }
                
                return {
                    "status": "failed",
                    "provider_reference": str(res_data.get("request-id") or request_id),
                    "error": message or "Purchase failed"
                }
                
        except Exception as exc:
            logger.error("TelecomAbode purchase exception: %s", exc)
            ambiguous_hints = (
                "timeout", "timed out", "connection error", "connection reset", 
                "non-json", "invalid json", "service unavailable", "remote protocol",
                "network error", "connecterror", "readerror", "transport", "http error"
            )
            msg = str(exc).lower()
            if any(hint in msg for hint in ambiguous_hints):
                return {"status": "pending", "error": f"Provider timeout/error: {str(exc)}"}
            return {"status": "failed", "error": str(exc)}

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
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=self._get_headers())
                logger.info("TelecomAbode POST %s network=%s phone=%s status=%d", 
                            url, network, phone, response.status_code)
                
                res_data = self._json_or_none(response) or {}
                status_value = str(res_data.get("status") or "").lower()
                inner_status = str(res_data.get("Status") or "").lower()
                message = str(res_data.get("message") or "")
                
                if status_value == "success" or inner_status == "successful":
                    return {"status": "success", "provider_reference": str(res_data.get("request-id") or request_id), "error": message}
                elif status_value == "pending" or inner_status == "pending":
                    return {"status": "pending", "provider_reference": str(res_data.get("request-id") or request_id), "error": message}
                
                return {"status": "failed", "provider_reference": str(res_data.get("request-id") or request_id), "error": message or "Data purchase failed"}
                
        except Exception as exc:
            logger.error("TelecomAbode data exception: %s", exc)
            return {"status": "pending", "error": f"Provider error: {str(exc)}"}

    def purchase_cable(self, provider: str, smartcard: str, plan_code: str, request_id: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"status": "failed", "error": "Telecom Abode API key is not configured"}
            
        url = f"{self.base_url}/cable"
        payload = {
            "cablename": provider.upper(),
            "smart_card_number": str(smartcard),
            "cableplan": str(plan_code),
            "request-id": str(request_id),
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=self._get_headers())
                res_data = self._json_or_none(response) or {}
                status_value = str(res_data.get("status") or "").lower()
                message = str(res_data.get("message") or "")
                
                if status_value == "success":
                    return {"status": "success", "provider_reference": str(res_data.get("request-id") or request_id), "error": message}
                if status_value == "pending":
                    return {"status": "pending", "provider_reference": str(res_data.get("request-id") or request_id), "error": message}
                return {"status": "failed", "provider_reference": str(res_data.get("request-id") or request_id), "error": message or "Cable purchase failed"}
        except Exception as exc:
            return {"status": "pending", "error": f"Provider error: {str(exc)}"}
