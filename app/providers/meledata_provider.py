import json
import logging
import httpx
from typing import Dict, Any, Optional
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.integration import IntegrationProvider

settings = get_settings()
logger = logging.getLogger(__name__)

# Persistent connection pool with Keep-Alive to eliminate TLS/TCP handshake latency
_MELEDATA_CLIENT = httpx.Client(
    timeout=httpx.Timeout(connect=8.0, read=25.0, write=10.0, pool=10.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=60.0),
)


class MeleDataProvider:
    """
    Integration provider for MELE DATA developer API.
    Used for sending Airtime top-ups and Data bundles via Mele Data's developer docs.
    """
    def __init__(self, db=None, api_key: Optional[str] = None, base_url: Optional[str] = None):
        _db = db or SessionLocal()
        try:
            db_prov = _db.query(IntegrationProvider).filter(IntegrationProvider.identifier == "meledata").first()
            raw_url = str(base_url or (db_prov.base_url if db_prov else None) or settings.meledata_base_url or "https://api.meledata.ng/api/v1").strip().rstrip("/")
            for bad_host in ("https://www.meledata.ng", "https://meledata.ng", "http://www.meledata.ng", "http://meledata.ng"):
                if raw_url.lower().startswith(bad_host):
                    raw_url = "https://api.meledata.ng" + raw_url[len(bad_host):]
                    break
            if raw_url.endswith("/developer"):
                raw_url = raw_url[:-10].rstrip("/")
            if not raw_url.endswith("/api/v1"):
                raw_url = f"{raw_url}/api/v1"
            self.base_url = raw_url
            raw_key = (api_key or (db_prov.api_key if db_prov else None) or settings.meledata_api_key or "").strip()
            # Force live mode key when calling Mele Data as an upstream provider so customer orders never hit sandbox
            if raw_key.startswith("mele_test_pub_"):
                raw_key = "mele_pub_" + raw_key[len("mele_test_pub_"):]
            elif raw_key.startswith("mele_test_"):
                raw_key = "mele_live_" + raw_key[len("mele_test_"):]
            if db_prov:
                dirty = False
                if db_prov.api_key and db_prov.api_key.strip() != raw_key:
                    db_prov.api_key = raw_key
                    dirty = True
                if db_prov.base_url != raw_url:
                    db_prov.base_url = raw_url
                    dirty = True
                if dirty:
                    try:
                        _db.commit()
                    except Exception:
                        _db.rollback()
            self.api_key = raw_key
        finally:
            if db is None:
                _db.close()
        self.timeout = 25.0

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key
        return headers

    def purchase_airtime(self, network: str, phone: str, amount: float, reference: str) -> Dict[str, Any]:
        """
        Connects to Mele Data Airtime API per Developer Docs:
        POST /api/v1/developer/airtime/purchase
        Payload: { "network": "mtn", "phone_number": "...", "amount": 500, "reference": "..." }
        """
        url = f"{self.base_url}/developer/airtime/purchase"
        payload = {
            "network": str(network).strip().lower(),
            "phone_number": str(phone).strip(),
            "amount": float(amount),
            "reference": str(reference).strip()
        }

        try:
            response = _MELEDATA_CLIENT.post(url, json=payload, headers=self._get_headers())
            if response.status_code == 404 and "application/json" not in (response.headers.get("content-type") or "").lower():
                alt_url = url.replace("/api/v1/developer", "/developer") if "/api/v1/developer" in url else url.replace("/developer", "/api/v1/developer")
                if alt_url != url:
                    logger.warning("MeleData airtime 404 on %s, trying %s", url, alt_url)
                    alt_resp = _MELEDATA_CLIENT.post(alt_url, json=payload, headers=self._get_headers())
                    if "application/json" in (alt_resp.headers.get("content-type") or "").lower():
                        response = alt_resp

            logger.info(
                "MeleData POST /developer/airtime/purchase network=%s phone=%s amount=%s status=%d",
                network, phone, amount, response.status_code
            )

            try:
                res_data = response.json()
            except Exception:
                return {
                    "status": False,
                    "message": f"Non-JSON response (Status {response.status_code}): {response.text[:200]}"
                }
            return res_data
        except Exception as e:
            logger.error("MeleData purchase_airtime error: %s", e)
            return {"status": False, "message": str(e)}

    def _resolve_remote_plan_id(self, network_str: str, clean_plan_id: str, data_size: Optional[str] = None, plan_name: Optional[str] = None) -> Optional[int]:
        """
        Queries GET /developer/data/plans on Mele Data to resolve the active integer plan_id
        when a local plan code (e.g. 'meledata:mtn:456') uses a provider suffix ('456') rather than
        Mele Data's primary key DataPlan.id.
        """
        try:
            plans_url = f"{self.base_url}/developer/data/plans"
            resp = _MELEDATA_CLIENT.get(plans_url, headers=self._get_headers())
            if resp.status_code != 200:
                return None
            body = resp.json()
            plans = (body.get("data") or {}).get("plans") or []
            net_upper = network_str.upper()
            net_plans = [p for p in plans if str(p.get("network") or "").upper() == net_upper]
            if not net_plans:
                return None

            # 1. Exact or suffix match on plan_code (e.g. 'mtn:456' or '*:456')
            target_low = clean_plan_id.lower()
            for p in net_plans:
                p_code = str(p.get("plan_code") or "").strip().lower()
                if p_code == target_low or p_code == f"{network_str}:{target_low}" or p_code.endswith(f":{target_low}"):
                    return int(p["plan_id"])

            # 2. Match by normalized data_size (e.g. '1gb', '500mb', '2gb') — pick lowest-priced active plan
            def _norm_size(s: Optional[str]) -> str:
                if not s:
                    return ""
                s_clean = str(s).strip().lower().replace(" ", "")
                if s_clean.endswith(".0gb"):
                    s_clean = s_clean[:-4] + "gb"
                if s_clean.endswith(".0mb"):
                    s_clean = s_clean[:-4] + "mb"
                return s_clean

            target_size = _norm_size(data_size)
            if target_size:
                size_matches = [p for p in net_plans if _norm_size(p.get("data_size")) == target_size]
                if size_matches:
                    size_matches.sort(key=lambda x: float(x.get("price") or 999999))
                    return int(size_matches[0]["plan_id"])
        except Exception as exc:
            logger.warning("MeleData _resolve_remote_plan_id failed: %s", exc)
        return None

    def purchase_data(
        self,
        network_id: Any,
        plan_id: str,
        phone: str,
        reference: str,
        data_size: Optional[str] = None,
        plan_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Connects to Mele Data Data API per Developer Docs:
        POST /api/v1/developer/data/purchase
        """
        url = f"{self.base_url}/developer/data/purchase"

        network_map = {
            1: "mtn",
            2: "airtel",
            3: "glo",
            4: "9mobile"
        }
        if str(network_id).isdigit():
            network_str = network_map.get(int(network_id), str(network_id).lower())
        else:
            network_str = str(network_id).strip().lower()

        clean_plan_id = str(plan_id).strip()
        if ":" in clean_plan_id:
            clean_plan_id = clean_plan_id.split(":")[-1]
        elif clean_plan_id.startswith("meledata.") and clean_plan_id.count(".") >= 2:
            clean_plan_id = clean_plan_id.split(".")[-1]

        payload = {
            "network": network_str,
            "phone_number": str(phone),
            "plan_id": int(clean_plan_id) if clean_plan_id.isdigit() else clean_plan_id,
            "reference": str(reference)
        }

        try:
            response = _MELEDATA_CLIENT.post(url, json=payload, headers=self._get_headers())
            if response.status_code == 404 and "application/json" not in (response.headers.get("content-type") or "").lower():
                alt_url = url.replace("/api/v1/developer", "/developer") if "/api/v1/developer" in url else url.replace("/developer", "/api/v1/developer")
                if alt_url != url:
                    logger.warning("MeleData data 404 on %s, trying %s", url, alt_url)
                    alt_resp = _MELEDATA_CLIENT.post(alt_url, json=payload, headers=self._get_headers())
                    if "application/json" in (alt_resp.headers.get("content-type") or "").lower():
                        response = alt_resp

            # If Mele Data returned 404 JSON ("Active data plan not found"), resolve the live plan_id from /developer/data/plans and retry
            if response.status_code == 404:
                resolved_id = self._resolve_remote_plan_id(network_str, clean_plan_id, data_size=data_size, plan_name=plan_name)
                if resolved_id is not None and str(resolved_id) != str(payload["plan_id"]):
                    logger.info(
                        "MeleData resolved plan '%s' (%s %s) -> remote plan_id=%d; retrying purchase",
                        plan_id, network_str, data_size or "", resolved_id
                    )
                    payload["plan_id"] = resolved_id
                    response = _MELEDATA_CLIENT.post(url, json=payload, headers=self._get_headers())

            logger.info(
                "MeleData POST /developer/data/purchase network=%s plan=%s phone=%s status=%d",
                network_str, payload["plan_id"], phone, response.status_code
            )

            try:
                res_data = response.json()
            except Exception:
                return {
                    "status": False,
                    "message": f"Non-JSON response (Status {response.status_code}): {response.text[:200]}"
                }
            return res_data
        except Exception as e:
            logger.error("MeleData purchase_data error: %s", e)
            return {"status": False, "message": str(e)}

    def purchase_network_data(
        self,
        network_id: Any,
        phone: str,
        plan_id: str,
        client_request_id: str,
        data_size: Optional[str] = None,
        plan_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        res = self.purchase_data(
            network_id=network_id,
            plan_id=plan_id,
            phone=phone,
            reference=client_request_id,
            data_size=data_size,
            plan_name=plan_name,
        )
        is_success = res.get("status") is True
        message = str(res.get("message") or res.get("msg") or res.get("detail") or "")
        data_obj_check = res.get("data") if isinstance(res.get("data"), dict) else {}
        if "sandbox" in message.lower() or data_obj_check.get("livemode") is False or str(data_obj_check.get("mode") or "").lower() == "test":
            return {
                "status": "failed",
                "provider_reference": str(data_obj_check.get("reference") or client_request_id),
                "error": "Upstream provider returned a Sandbox/Test response instead of Live delivery"
            }

        if is_success:
            data_obj = res.get("data") or {}
            inner_status = str(data_obj.get("status") or "").strip().lower()
            provider_reference = str(data_obj.get("reference") or res.get("reference") or client_request_id)

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

        status_value = str(res.get("status") or "").strip().lower()
        provider_reference = str(res.get("reference") or res.get("data", {}).get("reference") or client_request_id)
        if status_value == "success":
            return {
                "status": "success",
                "provider_reference": provider_reference,
                "error": message
            }

        ambiguous_hints = (
            "timeout", "timed out", "connection error", "connection reset",
            "service unavailable", "remote protocol", "network error"
        )
        lowered = message.lower()
        if status_value == "pending" or any(hint in lowered for hint in ambiguous_hints):
            return {
                "status": "pending",
                "provider_reference": provider_reference,
                "error": message
            }

        return {
            "status": "failed",
            "provider_reference": provider_reference,
            "error": message or f"Purchase failed: {json.dumps(res)}"
        }
