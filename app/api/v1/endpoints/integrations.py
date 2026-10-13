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
# Provider Plans Discovery
# -----------------

@router.get("/providers/{identifier}/plans")
def get_provider_plans(identifier: str, network: Optional[str] = None, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """
    Fetch live data plan catalog from a supported integrated provider.
    Used by admin data plans management to inspect and import plan IDs directly.
    """
    clean_identifier = (identifier or "").strip().lower()
    plans = []

    # 1. SMEPlug
    if clean_identifier == "smeplug":
        try:
            from app.providers.smeplug_provider import SMEPlugProvider
            sme = SMEPlugProvider(db=db)
            all_plans = sme.get_all_plans()
            if network:
                plans = [p for p in all_plans if str(p.get("network", "")).lower() == network.strip().lower()]
            else:
                plans = all_plans
            return {"status": True, "provider": "smeplug", "plans": plans}
        except Exception as e:
            return {"status": False, "message": f"Failed to fetch SMEPlug plans: {str(e)}", "plans": []}

    # 2. Amigo
    elif clean_identifier == "amigo":
        try:
            from app.api.v1.endpoints.data import get_amigo_client
            amigo = get_amigo_client(db)
            res = amigo.fetch_data_plans()
            raw_items = res.get("data", []) if isinstance(res, dict) else []
            for item in raw_items:
                nw = str(item.get("network") or "").lower()
                if network and nw != network.strip().lower():
                    continue
                plans.append({
                    "network": nw,
                    "plan_code": item.get("plan_code") or f"amigo:{nw}:{item.get('plan_id')}",
                    "plan_name": item.get("plan_name") or item.get("name"),
                    "data_size": item.get("data_size") or item.get("size") or item.get("plan_name"),
                    "price": float(item.get("price") or item.get("amount") or 0),
                    "provider": "amigo",
                    "provider_plan_id": str(item.get("plan_id") or item.get("id") or "")
                })
            return {"status": True, "provider": "amigo", "plans": plans}
        except Exception as e:
            return {"status": False, "message": f"Failed to fetch Amigo plans: {str(e)}", "plans": []}

    # 3. ClubKonnect
    elif clean_identifier == "clubkonnect":
        try:
            from app.services.bills import get_bills_provider
            ck = get_bills_provider()
            if hasattr(ck, "fetch_data_variations"):
                target_nw = network or "9mobile"
                raw_items = ck.fetch_data_variations(target_nw)
                for item in raw_items:
                    plans.append({
                        "network": str(item.get("network") or target_nw).lower(),
                        "plan_code": f"clubkonnect:{target_nw}:{item.get('Variation_Code') or item.get('id')}",
                        "plan_name": item.get("name") or item.get("package"),
                        "data_size": item.get("name") or item.get("package"),
                        "price": float(item.get("price") or item.get("amount") or 0),
                        "provider": "clubkonnect",
                        "provider_plan_id": str(item.get("Variation_Code") or item.get("id") or "")
                    })
            return {"status": True, "provider": "clubkonnect", "plans": plans}
        except Exception as e:
            return {"status": False, "message": f"Failed to fetch ClubKonnect plans: {str(e)}", "plans": []}

    # 4. Generic / MELE DATA / Custom Provider
    else:
        prov = db.query(IntegrationProvider).filter(IntegrationProvider.identifier == clean_identifier).first()
        base_url = str((prov.base_url if prov else None) or ("https://api.meledata.ng/api/v1" if clean_identifier == "meledata" else "")).rstrip("/")
        api_key = (prov.api_key if prov else "") or ""

        if not base_url or not api_key:
            return {
                "status": False,
                "message": f"Provider '{clean_identifier}' is missing API Base URL or API Key in Integrations settings.",
                "plans": []
            }

        import requests
        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-API-Key": api_key,
            "Accept": "application/json"
        }

        candidates = [
            f"{base_url}/developer/data/plans",
            f"{base_url}/data/plans",
            f"{base_url}/api/v1/developer/data/plans",
            f"{base_url}/api/v1/data/plans",
        ]

        last_error = "Could not reach upstream provider"
        for url in candidates:
            try:
                resp = requests.get(url, headers=headers, timeout=12)
                if resp.status_code == 200:
                    data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
                    raw_plans = None
                    if isinstance(data, dict):
                        if isinstance(data.get("data"), dict) and "plans" in data["data"]:
                            raw_plans = data["data"]["plans"]
                        elif isinstance(data.get("data"), list):
                            raw_plans = data["data"]
                        elif isinstance(data.get("plans"), list):
                            raw_plans = data["plans"]
                    elif isinstance(data, list):
                        raw_plans = data

                    if isinstance(raw_plans, list) and len(raw_plans) > 0:
                        parsed_plans = []
                        for p in raw_plans:
                            nw = str(p.get("network") or "").lower()
                            if network and nw != network.strip().lower():
                                continue
                            p_id = str(p.get("plan_id") or p.get("id") or p.get("plan_code") or "")
                            parsed_plans.append({
                                "network": nw,
                                "plan_code": p.get("plan_code") or f"{clean_identifier}:{nw}:{p_id}",
                                "plan_name": p.get("plan_name") or p.get("name"),
                                "data_size": p.get("data_size") or p.get("size") or p.get("plan_name"),
                                "price": float(p.get("price") or p.get("amount") or 0),
                                "provider": clean_identifier,
                                "provider_plan_id": p_id
                            })
                        return {"status": True, "provider": clean_identifier, "plans": parsed_plans}
                elif resp.status_code in (401, 403):
                    last_error = f"Authentication failed (HTTP {resp.status_code}). Please verify your API key."
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
            except Exception as e:
                last_error = str(e)
                continue

        return {"status": False, "message": f"{clean_identifier.upper()} error: {last_error}", "plans": []}


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

    # 4. Generic / MELE DATA / Custom Provider
    else:
        if not base_url:
            return {"status": False, "message": "API Base URL is required to test custom provider connection."}
        headers = {"Authorization": f"Bearer {api_key}", "X-API-Key": api_key} if api_key else {}
        for endpoint in ["/developer/wallet/balance", "/developer/user", "/wallet/balance", "/account/balance", "/balance", "/user", ""]:
            try:
                test_url = f"{base_url}{endpoint}"
                resp = requests.get(test_url, headers=headers, timeout=10)
                if resp.status_code in (200, 201):
                    data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {"text": resp.text}
                    bal = None
                    if isinstance(data, dict):
                        if isinstance(data.get("data"), dict) and "balance" in data["data"]:
                            bal = data["data"]["balance"]
                        elif "balance" in data:
                            bal = data["balance"]
                    return {
                        "status": True,
                        "message": f"Connected successfully to {test_url} (HTTP {resp.status_code})",
                        "balance": float(bal) if bal is not None else None,
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
