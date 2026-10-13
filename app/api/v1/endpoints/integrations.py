from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid

from app.core.database import get_db
from app.models.integration import IntegrationProvider, PaymentGateway
from app.dependencies import require_admin

router = APIRouter()

# -----------------
# API Integrations
# -----------------

class ProviderBase(BaseModel):
    name: str
    identifier: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    additional_config: Optional[Dict[str, Any]] = {}
    is_active: bool = False
    supported_services: Optional[List[str]] = []

class ProviderCreate(ProviderBase):
    pass

class ProviderResponse(ProviderBase):
    id: uuid.UUID
    balance: float
    
    class Config:
        orm_mode = True

@router.get("/providers", response_model=List[ProviderResponse])
def get_providers(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return db.query(IntegrationProvider).all()

@router.post("/providers", response_model=ProviderResponse)
def create_provider(provider: ProviderCreate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    existing = db.query(IntegrationProvider).filter(IntegrationProvider.identifier == provider.identifier).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provider with this identifier already exists")
        
    db_obj = IntegrationProvider(**provider.dict())
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

# -----------------
# Provider Connection Testing (Must be placed before /{provider_id} route)
# -----------------

class TestProviderRequest(BaseModel):
    identifier: Optional[str] = None
    provider_id: Optional[uuid.UUID] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None

class TestProviderResponse(BaseModel):
    status: bool
    message: str
    balance: Optional[float] = None
    raw_response: Optional[Any] = None

@router.post("/providers/test", response_model=TestProviderResponse)
def test_provider_connection(payload: TestProviderRequest, db: Session = Depends(get_db), admin=Depends(require_admin)):
    import requests
    identifier = (payload.identifier or "").strip().lower()
    base_url = (payload.base_url or "").strip().rstrip("/")
    api_key = (payload.api_key or "").strip()

    # If api_key was not sent from client, look it up in the database
    if not api_key:
        prov = None
        if payload.provider_id:
            prov = db.query(IntegrationProvider).filter(IntegrationProvider.id == payload.provider_id).first()
        elif identifier:
            prov = db.query(IntegrationProvider).filter(IntegrationProvider.identifier == identifier).first()
        if prov:
            api_key = prov.api_key or ""
            base_url = (prov.base_url or "").rstrip("/")
            identifier = prov.identifier.lower()

    if not api_key:
        return {"status": False, "message": "No API key found for this provider. Please configure an API key first."}

    # 1. SMEPlug
    if identifier == "smeplug":
        url_candidates = [
            f"{base_url}/account/balance" if base_url else "https://smeplug.ng/api/v1/account/balance",
            f"{base_url}/wallet/balance" if base_url else "https://smeplug.ng/api/v1/wallet/balance",
        ]
        headers = {"Authorization": f"Bearer {api_key}"}
        last_error = "Connection failed"
        for url in url_candidates:
            try:
                resp = requests.get(url, headers=headers, timeout=12)
                data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {"text": resp.text}
                if resp.status_code == 200:
                    bal = None
                    if isinstance(data, dict):
                        if isinstance(data.get("data"), dict):
                            bal = data["data"].get("balance")
                        elif "balance" in data:
                            bal = data.get("balance")
                    return {
                        "status": True,
                        "message": "Connection to SMEPlug verified successfully!",
                        "balance": float(bal) if bal is not None else None,
                        "raw_response": data
                    }
                err_msg = data.get("msg") or data.get("message") if isinstance(data, dict) else resp.text
                last_error = f"HTTP {resp.status_code}: {err_msg}"
            except Exception as e:
                last_error = str(e)
        return {"status": False, "message": f"SMEPlug error: {last_error}"}

    # 2. Amigo
    elif identifier == "amigo":
        url = f"{base_url}/user" if base_url else "https://amigo.ng/api/user"
        headers = {"Authorization": f"Bearer {api_key}", "Token": api_key}
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {"text": resp.text}
            if resp.status_code == 200:
                bal = data.get("balance") if isinstance(data, dict) else None
                return {
                    "status": True,
                    "message": "Connection to Amigo verified successfully!",
                    "balance": float(bal) if bal is not None else None,
                    "raw_response": data
                }
            return {
                "status": False,
                "message": f"Amigo returned HTTP {resp.status_code}",
                "raw_response": data
            }
        except Exception as e:
            return {"status": False, "message": f"Connection error to Amigo: {str(e)}"}

    # 3. ClubKonnect
    elif identifier == "clubkonnect":
        try:
            from app.services.bills import ClubKonnectBillsProvider
            ck = ClubKonnectBillsProvider()
            bal = ck.get_balance()
            return {
                "status": True,
                "message": f"ClubKonnect balance retrieved successfully",
                "balance": float(bal) if isinstance(bal, (int, float)) else None,
                "raw_response": {"balance": bal}
            }
        except Exception as e:
            return {"status": False, "message": f"ClubKonnect error: {str(e)}"}

    # 4. Generic Provider
    else:
        if not base_url:
            return {"status": False, "message": "API Base URL is required to test custom provider connection."}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        for endpoint in ["/account/balance", "/wallet/balance", "/balance", "/user", ""]:
            try:
                test_url = f"{base_url}{endpoint}"
                resp = requests.get(test_url, headers=headers, timeout=10)
                if resp.status_code in (200, 201):
                    data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {"text": resp.text}
                    return {
                        "status": True,
                        "message": f"Connected successfully to {test_url} (HTTP {resp.status_code})",
                        "raw_response": data
                    }
            except Exception:
                continue
        return {"status": False, "message": f"Could not reach {base_url} with provided credentials."}


@router.put("/providers/{provider_id}", response_model=ProviderResponse)
def update_provider(provider_id: uuid.UUID, provider: ProviderBase, db: Session = Depends(get_db), admin=Depends(require_admin)):
    db_obj = db.query(IntegrationProvider).filter(IntegrationProvider.id == provider_id).first()
    if not db_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
        
    for key, value in provider.dict(exclude_unset=True).items():
        setattr(db_obj, key, value)
        
    db.commit()
    db.refresh(db_obj)
    return db_obj

@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(provider_id: uuid.UUID, db: Session = Depends(get_db), admin=Depends(require_admin)):
    db_obj = db.query(IntegrationProvider).filter(IntegrationProvider.id == provider_id).first()
    if not db_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
        
    db.delete(db_obj)
    db.commit()


# -----------------
# Payment Gateways
# -----------------

class GatewayBase(BaseModel):
    name: str
    identifier: str
    public_key: Optional[str] = None
    secret_key: Optional[str] = None
    contract_code: Optional[str] = None
    webhook_url: Optional[str] = None
    additional_config: Optional[Dict[str, Any]] = {}
    charge_percentage: float = 0.0
    charge_flat: float = 0.0
    is_active: bool = False

class GatewayCreate(GatewayBase):
    pass

class GatewayResponse(GatewayBase):
    id: uuid.UUID
    
    class Config:
        orm_mode = True

@router.get("/gateways", response_model=List[GatewayResponse])
def get_gateways(db: Session = Depends(get_db), admin=Depends(require_admin)):
    return db.query(PaymentGateway).all()

@router.post("/gateways", response_model=GatewayResponse)
def create_gateway(gateway: GatewayCreate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    existing = db.query(PaymentGateway).filter(PaymentGateway.identifier == gateway.identifier).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gateway with this identifier already exists")
        
    db_obj = PaymentGateway(**gateway.dict())
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

@router.put("/gateways/{gateway_id}", response_model=GatewayResponse)
def update_gateway(gateway_id: uuid.UUID, gateway: GatewayBase, db: Session = Depends(get_db), admin=Depends(require_admin)):
    db_obj = db.query(PaymentGateway).filter(PaymentGateway.id == gateway_id).first()
    if not db_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
        
    for key, value in gateway.dict(exclude_unset=True).items():
        setattr(db_obj, key, value)
        
    db.commit()
    db.refresh(db_obj)
    return db_obj

@router.delete("/gateways/{gateway_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_gateway(gateway_id: uuid.UUID, db: Session = Depends(get_db), admin=Depends(require_admin)):
    db_obj = db.query(PaymentGateway).filter(PaymentGateway.id == gateway_id).first()
    if not db_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")
        
    db.delete(db_obj)
    db.commit()
