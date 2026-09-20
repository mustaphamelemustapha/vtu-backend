import logging
import httpx
from typing import Dict, Any
from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

class MZDataProvider:
    def __init__(self):
        self.base_url = str(settings.mzdata_base_url).rstrip("/")
        self.api_key = settings.mzdata_api_key
        self.timeout = 30.0

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _json_or_none(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return None

    def purchase_data(self, network_id: int, plan_id: str, phone: str, reference: str) -> dict:
        url = f"{self.base_url}/developer/data/purchase"
        
        # Network mapping according to MZDATA API
        # MTN: 1, AIRTEL: 2, GLO: 3, 9MOBILE: 4
        network_map = {
            1: "mtn",
            2: "airtel",
            3: "glo",
            4: "9mobile"
        }
        network_str = network_map.get(int(network_id), "mtn")
        
        if ":" in str(plan_id):
            plan_id = str(plan_id).split(":")[-1]
            
        payload = {
            "network": network_str,
            "phone_number": str(phone),
            "plan_id": int(plan_id) if str(plan_id).isdigit() else plan_id,
            "reference": str(reference)
        }
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=self._get_headers())
                logger.info("MZData POST /developer/data/purchase network=%s plan=%s phone=%s status=%d", 
                            network_str, plan_id, phone, response.status_code)
                
                if response.status_code not in (200, 201):
                    logger.error("MZData purchase error: status=%d response=%s", response.status_code, response.text)
                
                res_data = self._json_or_none(response)
                if res_data is None:
                    return {"status": "failed", "message": f"Non-JSON response (Status {response.status_code}): {response.text[:200]}"}
                return res_data
        except Exception as e:
            logger.error(f"MZData purchase_data error: {e}")
            return {"status": "failed", "message": str(e)}

    def purchase_network_data(self, network_id: int, phone: str, plan_id: str, client_request_id: str) -> Dict[str, Any]:
        res = self.purchase_data(network_id=network_id, plan_id=plan_id, phone=phone, reference=client_request_id)
        
        is_success = res.get("status") is True
        message = str(res.get("message") or res.get("msg") or res.get("detail") or "")
        
        if is_success:
            data_obj = res.get("data") or {}
            inner_status = str(data_obj.get("status") or "").strip().lower()
            provider_reference = str(data_obj.get("reference") or res.get("reference") or "")
            
            if inner_status in ("success", "successful", "delivered"):
                return {
                    "status": "success",
                    "provider_reference": provider_reference,
                    "error": message
                }
            elif inner_status in ("pending", "processing"):
                return {
                    "status": "pending",
                    "provider_reference": provider_reference,
                    "error": message
                }
            else:
                return {
                    "status": "failed",
                    "provider_reference": provider_reference,
                    "error": message or "Provider reported failure in data block"
                }

        # Fallback for error responses or backward compatibility
        status_value = str(res.get("status") or "").strip().lower()
        provider_reference = str(res.get("reference") or res.get("data", {}).get("reference") or "")
        
        if status_value == "success":
            return {
                "status": "success",
                "provider_reference": provider_reference,
                "error": message
            }
            
        ambiguous_hints = (
            "timeout", "timed out", "connection error", "connection reset", 
            "service unavailable", "remote protocol",
            "network error", "connecterror", "readerror", "transport", "http error"
        )
        lowered = message.lower()
        
        if status_value == "pending" or "processing" in lowered or "pending" in lowered or any(hint in lowered for hint in ambiguous_hints):
            return {
                "status": "pending",
                "provider_reference": provider_reference,
                "error": message
            }
            
        import json
        return {
            "status": "failed",
            "provider_reference": provider_reference,
            "error": message or f"Purchase failed: {json.dumps(res)}"
        }
