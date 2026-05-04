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

    def _json_or_none(self, response: httpx.Response) -> Dict[str, Any] | None:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        except Exception:
            return None
        return None

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
        # Per SMEPlug docs: POST /api/v1/data/purchase
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
                logger.info("SMEPlug Purchase Response status=%s body=%s", response.status_code, response.text)

                if response.status_code >= 500:
                    return {
                        "status": "pending",
                        "provider_reference": None,
                        "error": "Provider server error"
                    }

                data = self._json_or_none(response) or {}
                if not data:
                    # Non-JSON from provider: ambiguous, keep pending for reconcile/query.
                    return {
                        "status": "pending",
                        "provider_reference": None,
                        "error": "Awaiting provider confirmation"
                    }

                status_value = data.get("status")
                data_node = data.get("data") if isinstance(data.get("data"), dict) else {}
                message = str(
                    data.get("msg")
                    or data.get("message")
                    or data_node.get("msg")
                    or ""
                ).strip()

                # Typical success shape:
                # { "status": "success", "data": { "reference": "...", ... } }
                if status_value is True or str(status_value).strip().lower() in {"success", "true"}:
                    resp_data = data_node
                    return {
                        "status": "pending",
                        "provider_reference": str(resp_data.get("reference") or ""),
                        "error": message,
                    }

                # Some provider 400/failed responses are still eventually processed;
                # keep those as pending to avoid false-negative instant refunds.
                lowered = message.lower()
                if (
                    response.status_code in {400, 409, 422}
                    and ("processing" in lowered or "pending" in lowered or "failed" in lowered)
                ):
                    return {
                        "status": "pending",
                        "provider_reference": str((data.get("data") or {}).get("reference") or ""),
                        "error": message or "Awaiting provider confirmation",
                    }

                return {
                    "status": "failed",
                    "provider_reference": str(data_node.get("reference") or ""),
                    "error": message or "Purchase failed",
                }
        except Exception as exc:
            logger.error("SMEPlug purchase exception: %s", exc)
            return {
                "status": "pending",
                "error": str(exc)
            }

    def query_transaction(self, reference: str) -> Dict[str, Any]:
        ref = str(reference or "").strip()
        if not ref:
            return {"status": "pending", "error": "Missing reference"}
        url = f"{self.base_url}/transactions/{ref}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._headers())
                logger.info("SMEPlug Query Response status=%s body=%s", response.status_code, response.text)
                data = self._json_or_none(response) or {}
                if response.status_code >= 500:
                    return {"status": "pending", "error": "Provider query unavailable"}
                if not data:
                    return {"status": "pending", "error": "Provider query unavailable"}

                status_value = str(data.get("status") or "").strip().lower()
                provider_reference = str(data.get("reference") or "")
                message = str(data.get("response") or data.get("message") or data.get("memo") or "")

                if status_value == "success":
                    return {"status": "success", "provider_reference": provider_reference, "error": message}
                if status_value == "failed":
                    return {"status": "failed", "provider_reference": provider_reference, "error": message or "Provider reported failure"}
                return {"status": "pending", "provider_reference": provider_reference, "error": message}
        except Exception as exc:
            logger.error("SMEPlug query exception: %s", exc)
            return {"status": "pending", "error": str(exc)}
