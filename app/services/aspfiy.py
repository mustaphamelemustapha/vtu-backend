import logging
import httpx
from fastapi import HTTPException
from app.core.config import get_settings

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
        if not settings.aspfiy_secret_key:
            logger.warning("Aspfiy is not configured (missing secret key)")
            raise HTTPException(status_code=500, detail="Aspfiy is not configured")

        url = f"{str(settings.aspfiy_base_url).rstrip('/')}/reserve-paga/"
        
        # Determine a webhook URL
        webhook_url = f"{settings.frontend_base_url.rstrip('/')}/api/v1/webhooks/aspfiy"
        if not webhook_url.startswith("https://") and not webhook_url.startswith("http://"):
            webhook_url = "https://meledata.ng/api/v1/webhooks/aspfiy" # Fallback if dev
            
        payload = {
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "phone": phone,
            "reference": reference,
            "webhookUrl": webhook_url
        }

        headers = {
            "Authorization": f"Bearer {settings.aspfiy_secret_key}",
            "Content-Type": "application/json"
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(url, json=payload, headers=headers)
                
            response.raise_for_status()
            data = response.json()
            
            # Based on typical Paga/Aspfiy response, the exact structure is not in the prompt
            # but we assume it contains the account details.
            # E.g. {"status": True, "data": {"accountNumber": "...", "accountName": "...", "bankName": "..."}}
            # Let's return the raw JSON and parse it in the caller.
            return data
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Aspfiy HTTP Error: {e.response.text}")
            raise HTTPException(status_code=502, detail="Failed to create Aspfiy account")
        except Exception as e:
            logger.error(f"Aspfiy request failed: {e}")
            raise HTTPException(status_code=502, detail="Error communicating with Aspfiy")
