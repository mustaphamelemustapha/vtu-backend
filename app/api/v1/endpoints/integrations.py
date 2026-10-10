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
