import logging
import httpx
from typing import List, Dict, Any
from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

class SMEPlugProvider:
    def __init__(self):
        self.base_url = str(settings.smeplug_base_url).rstrip("/")
        self.api_key = settings.smeplug_api_key
        self.timeout = 20.0

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def get_airtel_plans(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/data/plans"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._headers())
                response.raise_for_status()
                data = response.json()
                
                # SMEPlug returns plans for all networks. Filter for Airtel (network_id=2).
                # Expected format based on typical SMEPlug response:
                # { "status": "success", "data": [ { "id": 1, "network_id": 2, "name": "...", "price": ... }, ... ] }
                plans = data.get("data", [])
                if not isinstance(plans, list):
                    logger.warning("SMEPlug plans response 'data' is not a list: %s", data)
                    return []

                airtel_network_id = int(settings.smeplug_network_airtel)
                airtel_plans = []
                for p in plans:
                    if int(p.get("network_id", 0)) == airtel_network_id:
                        airtel_plans.append({
                            "provider_plan_id": str(p.get("id")),
                            "name": p.get("name"),
                            "cost_price": float(p.get("price", 0)),
                            "network": "airtel",
                            "provider": "smeplug"
                        })
                return airtel_plans
        except Exception as exc:
            logger.error("Failed to fetch plans from SMEPlug: %s", exc)
            return []

    def purchase_airtel_data(self, phone: str, plan_id: str, client_request_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/data/purchase"
        payload = {
            "network_id": int(settings.smeplug_network_airtel),
            "plan_id": plan_id,
            "phone": phone,
            "customer_reference": client_request_id
        }
        
        logger.info("SMEPlug Purchase Request: %s", {**payload, "api_key": "***"})
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=self._headers())
                # Log response but don't raise immediately to handle error bodies
                logger.info("SMEPlug Purchase Response status=%s body=%s", response.status_code, response.text)
                
                if response.status_code >= 500:
                    return {
                        "status": "pending",
                        "provider_reference": None,
                        "error": "Provider server error"
                    }
                
                data = response.json()
                # SMEPlug typical response: { "status": "success", "data": { "reference": "...", ... } }
                if data.get("status") == "success":
                    resp_data = data.get("data") or {}
                    return {
                        "status": "pending", # We treat all initiated as pending until webhook/query
                        "provider_reference": str(resp_data.get("reference") or ""),
                    }
                else:
                    return {
                        "status": "failed",
                        "error": data.get("msg") or data.get("message") or "Purchase failed"
                    }
        except Exception as exc:
            logger.error("SMEPlug purchase exception: %s", exc)
            return {
                "status": "pending", # Ambiguous error, don't auto-fail
                "error": str(exc)
            }
