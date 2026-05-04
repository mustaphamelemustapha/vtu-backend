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
                
                # SMEPlug returns plans grouped by network ID in a dictionary.
                # '1' = MTN, '2' = Airtel, '3' = 9mobile, '4' = Glo
                plans_data = data.get("data", {})
                
                # If it's a dictionary, get the specific list for Airtel ('2')
                if isinstance(plans_data, dict):
                    raw_plans = plans_data.get("2", [])
                elif isinstance(plans_data, list):
                    # Fallback for flat list format
                    raw_plans = [p for p in plans_data if str(p.get("network_id")) == "2"]
                else:
                    logger.warning("Unexpected SMEPlug plans data format: %s", type(plans_data))
                    raw_plans = []

                results = []
                for p in raw_plans:
                    results.append({
                        "network": "airtel",
                        "plan_name": p.get("name"),
                        "plan_code": f"airtel:{p.get('id')}",
                        "data_size": p.get("name"), # SMEPlug doesn't provide explicit size field
                        "validity": "30 Days", # Defaulting as SMEPlug doesn't provide this in a standard field
                        "price": p.get("price") or p.get("telco_price") or 0,
                        "provider": "smeplug",
                        "provider_plan_id": str(p.get("id"))
                    })
                return results
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                logger.error("Failed to fetch plans from SMEPlug: 403 Forbidden. Hint: Check if your server IP is whitelisted on the SMEPlug dashboard and your API key is correct.")
            else:
                logger.error("Failed to fetch plans from SMEPlug: %s", exc)
            return []
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
