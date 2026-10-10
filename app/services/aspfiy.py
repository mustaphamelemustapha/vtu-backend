import logging
import httpx
from fastapi import HTTPException
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.integration import PaymentGateway

logger = logging.getLogger(__name__)
settings = get_settings()

class AspfiyService:
    @staticmethod
    def create_reserved_account(
        email: str,
        first_name: str,
        last_name: str,
        phone: str,
        reference: str
    ) -> dict:
        db = SessionLocal()
        try:
            db_settings = db.query(PaymentGateway).filter(PaymentGateway.identifier == "asfiy").first()
            if db_settings and db_settings.is_active:
                secret_key = db_settings.secret_key or settings.aspfiy_secret_key
                webhook_url = db_settings.webhook_url or settings.aspfiy_webhook_url or "https://vtu-backend-8gsi.onrender.com/api/v1/webhooks/aspfiy"
            else:
                secret_key = settings.aspfiy_secret_key
                webhook_url = settings.aspfiy_webhook_url or "https://vtu-backend-8gsi.onrender.com/api/v1/webhooks/aspfiy"
        finally:
            db.close()

        if not secret_key:
            logger.warning("Aspfiy is not configured (missing secret key)")
            raise HTTPException(status_code=500, detail="Aspfiy is not configured")

        url = f"{str(settings.aspfiy_base_url).rstrip('/')}/reserve-paga/"
        
        # Determine a backend webhook URL
        webhook_url = str(webhook_url).strip()
            
        payload = {
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "phone": phone,
            "reference": reference,
            "webhookUrl": webhook_url
        }

        headers = {
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json"
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(url, json=payload, headers=headers)
                
            response.raise_for_status()
            data = response.json()
            logger.info("Aspfiy reserve-paga response for ref %s: %s", reference, data)
            return data
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Aspfiy HTTP Error: {e.response.text}")
            raise HTTPException(status_code=502, detail="Failed to create Aspfiy account")
        except Exception as e:
            logger.error(f"Aspfiy request failed: {e}")
            raise HTTPException(status_code=502, detail="Error communicating with Aspfiy")

    @staticmethod
    def parse_reserved_account_response(resp: dict | list) -> dict | None:
        """
        Defensively extracts account_number, account_name, and bank_name from Aspfiy response.
        Handles responses formatted as:
        - {"status": True, "data": {"accountNumber": "...", "accountName": "...", "bankName": "..."}}
        - {"status": True, "data": [{"account_number": "...", ...}]}
        - {"status": True, "data": {"account": {"accountNumber": "...", ...}}}
        - {"account_number": "...", ...}
        - or any permutation of camelCase / snake_case / list.
        """
        if not resp:
            return None
        
        target = resp
        if isinstance(target, dict):
            target = target.get("data") or target

        if isinstance(target, list) and len(target) > 0:
            target = target[0]

        if isinstance(target, dict):
            if "account" in target and isinstance(target["account"], dict):
                target = target["account"]
            elif "accounts" in target and isinstance(target["accounts"], list) and len(target["accounts"]) > 0:
                target = target["accounts"][0]

        if not isinstance(target, dict):
            return None

        acc_num = (
            target.get("account_number")
            or target.get("accountNumber")
            or target.get("account_no")
            or target.get("accountNo")
            or target.get("account")
        )
        if not acc_num or str(acc_num).strip().lower() in ("", "none", "null"):
            return None

        acc_name = (
            target.get("account_name")
            or target.get("accountName")
            or target.get("customer_name")
            or target.get("customerName")
            or "Kulloma Data Customer"
        )
        bank_name = (
            target.get("bank_name")
            or target.get("bankName")
            or target.get("bank")
            or "Paga"
        )

        return {
            "account_number": str(acc_num).strip(),
            "account_name": str(acc_name).strip(),
            "bank_name": str(bank_name).strip(),
        }
