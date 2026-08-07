import logging
import httpx
from typing import Dict, Any, List
from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

class AutosyncProvider:
    def __init__(self):
        self.base_url = str(settings.autosync_base_url).rstrip("/")
        self.api_key = settings.autosync_api_key
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

    def fetch_gifting_plans(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/v2/data"
        return self._fetch_and_parse_plans(url, "gifting")

    def fetch_sme_plans(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/v2/data/sme"
        return self._fetch_and_parse_plans(url, "sme")

    def _fetch_and_parse_plans(self, url: str, plan_type: str) -> List[Dict[str, Any]]:
        results = []
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._get_headers())
                data = self._json_or_none(response)
                
                if not isinstance(data, dict) or data.get("status") != "ok":
                    logger.error(f"Autosync {plan_type} plans fetch failed: {data}")
                    return []
                
                category = data.get("data", {}).get("category", {})
                products = category.get("products", [])
                
                for product in products:
                    nw_name = product.get("code", "").lower()
                    if not nw_name:
                        continue
                        
                    groups = product.get("groups", [])
                    for group in groups:
                        validity = group.get("name", "30 Days")
                        if validity and validity.lower() == "others":
                            validity = "30 Days"
                            
                        variations = group.get("variations", [])
                        for variation in variations:
                            results.append({
                                "network": nw_name,
                                "plan_code": f"{nw_name}:{variation.get('code')}",
                                "plan_name": variation.get("name"),
                                "data_size": variation.get("name"), # We can extract size from name or just use name
                                "price": float(variation.get("amount") or 0),
                                "validity": validity,
                                "provider": "autosync",
                                "provider_plan_id": str(variation.get("code")),
                                "data_type": "Gifting" if plan_type == "gifting" else "SME"
                            })
        except Exception as e:
            logger.error(f"Autosync _fetch_and_parse_plans exception: {e}")
            
        return results

    def get_all_plans(self) -> List[Dict[str, Any]]:
        plans = []
        plans.extend(self.fetch_gifting_plans())
        plans.extend(self.fetch_sme_plans())
        return plans

    def purchase_network_data(self, network: str, phone: str, plan_id: str, client_request_id: str, data_type: str = "Gifting") -> Dict[str, Any]:
        """
        plan_id here is the variation code from Autosync.
        data_type determines the endpoint.
        """
        endpoint = "/v1/data/sme" if data_type.lower() == "sme" else "/v1/data"
        url = f"{self.base_url}{endpoint}"
        
        # Payload based on general VTU API standards 
        payload = {
            "network": network,
            "phone": phone,
            "data_plan": plan_id,
            "reference": client_request_id
        }
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=self._get_headers())
                logger.info("Autosync POST %s network=%s plan=%s phone=%s status=%d", 
                            endpoint, network, plan_id, phone, response.status_code)
                
                res_data = self._json_or_none(response) or {}
                
                status_value = str(res_data.get("status") or "").lower()
                message = str(res_data.get("message") or "")
                
                # "successful", "failed", "pending" according to docs
                if status_value == "successful":
                    return {
                        "status": "success",
                        "provider_reference": str(res_data.get("reference") or ""),
                        "error": message
                    }
                elif status_value == "pending":
                    return {
                        "status": "pending",
                        "provider_reference": str(res_data.get("reference") or ""),
                        "error": message
                    }
                
                return {
                    "status": "failed",
                    "provider_reference": str(res_data.get("reference") or ""),
                    "error": message or "Purchase failed"
                }
                
        except Exception as exc:
            logger.error("Autosync purchase exception: %s", exc)
            ambiguous_hints = (
                "timeout", "timed out", "connection error", "connection reset", 
                "non-json", "invalid json", "service unavailable", "remote protocol",
                "network error", "connecterror", "readerror", "transport", "http error"
            )
            msg = str(exc).lower()
            if any(hint in msg for hint in ambiguous_hints):
                return {"status": "pending", "error": f"Provider timeout/error: {str(exc)}"}
            return {"status": "failed", "error": str(exc)}

    def query_transaction(self, reference: str) -> Dict[str, Any]:
        url = f"{self.base_url}/v1/transactions/{reference}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._get_headers())
                data = self._json_or_none(response) or {}
                
                status_value = str(data.get("status") or "").strip().lower()
                provider_reference = str(data.get("reference") or "")
                message = str(data.get("message") or "")

                if status_value == "successful":
                    return {"status": "success", "provider_reference": provider_reference, "error": message}
                if status_value == "failed":
                    return {"status": "failed", "provider_reference": provider_reference, "error": message}
                return {"status": "pending", "provider_reference": provider_reference, "error": message}
        except Exception as exc:
            logger.error("Autosync query exception: %s", exc)
            return {"status": "pending", "error": str(exc)}
