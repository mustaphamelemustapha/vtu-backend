import logging
import httpx
import time
from typing import Dict, Any, List
from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

class SMEPlugProvider:
    def __init__(self):
        self.base_url = str(settings.smeplug_base_url).rstrip("/")
        self.api_key = settings.smeplug_api_key
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

    def fetch_plans(self) -> dict:
        url = f"{self.base_url}/data/plans"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._get_headers())
                logger.info("SMEPlug GET /data/plans status=%d", response.status_code)
                data = self._json_or_none(response)
                if isinstance(data, dict):
                    return data
                if isinstance(data, list):
                    return {"status": True, "data": data}
                return {"status": False, "msg": "Invalid response format"}
        except Exception as e:
            logger.error(f"SMEPlug fetch_plans error: {e}")
            return {"status": False, "msg": str(e)}

    def get_all_plans(self) -> List[Dict[str, Any]]:
        payload = self.fetch_plans()
        # SMEPlug usually returns {"status": true, "data": { "1": [...], "2": [...] }}
        # or sometimes "status" is missing but "data" is present.
        data = payload.get("data")
        if not data:
            # Fallback if the whole payload is the dictionary of networks
            data = payload if isinstance(payload, dict) and any(k in payload for k in ["1", "2", "3", "4"]) else None

        if not isinstance(data, dict):
            logger.warning("SMEPlug plans data is not a dict: %s", type(data))
            return []

        results = []
        # Documentation mapping: 1=MTN, 2=Airtel, 3=9mobile, 4=Glo
        id_map = {
            "1": "mtn",
            "2": "airtel",
            "3": "9mobile",
            "4": "glo"
        }

        for nw_id, plans in data.items():
            nw_name = id_map.get(str(nw_id))
            if not nw_name or not isinstance(plans, list):
                continue
            
            for p in plans:
                results.append({
                    "network": nw_name,
                    "plan_code": f"{nw_name}:{p.get('id')}",
                    "plan_name": p.get("name"),
                    "data_size": p.get("name"),
                    "price": float(p.get("price") or p.get("telco_price") or 0),
                    "provider": "smeplug",
                    "provider_plan_id": str(p.get("id"))
                })
        return results

    def get_airtel_plans(self) -> list:
        return [p for p in self.get_all_plans() if p.get("network") == "airtel"]

    def purchase_data(self, network_id: int, plan_id: str, phone: str, reference: str) -> dict:
        url = f"{self.base_url}/data/purchase"
        payload = {
            "network_id": int(network_id),
            "plan_id": int(plan_id) if str(plan_id).isdigit() else str(plan_id),
            "phone": str(phone),
            "customer_reference": str(reference)
        }
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=self._get_headers())
                logger.info("SMEPlug POST /data/purchase network=%s plan=%s phone=%s status=%d", 
                            network_id, plan_id, phone, response.status_code)
                
                if response.status_code != 200:
                    logger.error("SMEPlug purchase error: status=%d response=%s", response.status_code, response.text)
                
                res_data = self._json_or_none(response) or {}
                return res_data
        except Exception as e:
            logger.error(f"SMEPlug purchase_data error: {e}")
            return {"status": False, "msg": str(e)}

    def purchase_network_data(self, network_id: int, phone: str, plan_id: str, client_request_id: str) -> Dict[str, Any]:
        res = self.purchase_data(network_id=network_id, plan_id=plan_id, phone=phone, reference=client_request_id)
        
        status_value = res.get("status")
        message = str(res.get("msg") or res.get("message") or "")
        data_node = res.get("data") if isinstance(res.get("data"), dict) else {}
        
        if status_value is True or str(status_value).lower() in {"success", "true"}:
            return {
                "status": "success",
                "provider_reference": str(data_node.get("reference") or ""),
                "error": message
            }
        
        lowered = message.lower()
        if "processing" in lowered or "pending" in lowered:
            return {
                "status": "pending",
                "provider_reference": str(data_node.get("reference") or ""),
                "error": message
            }
            
        return {
            "status": "failed",
            "provider_reference": str(data_node.get("reference") or ""),
            "error": message or "Purchase failed"
        }

    def query_transaction(self, reference: str) -> Dict[str, Any]:
        url = f"{self.base_url}/transactions/{reference}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._get_headers())
                data = self._json_or_none(response) or {}
                
                status_value = str(data.get("status") or "").strip().lower()
                provider_reference = str(data.get("reference") or "")
                message = str(data.get("response") or data.get("message") or "")

                if status_value == "success":
                    return {"status": "success", "provider_reference": provider_reference, "error": message}
                if status_value == "failed":
                    return {"status": "failed", "provider_reference": provider_reference, "error": message}
                return {"status": "pending", "provider_reference": provider_reference, "error": message}
        except Exception as exc:
            logger.error("SMEPlug query exception: %s", exc)
            return {"status": "pending", "error": str(exc)}
