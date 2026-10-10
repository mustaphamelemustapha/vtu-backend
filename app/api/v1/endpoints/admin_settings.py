from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db
from app.models.system_settings import SystemSettings
from app.dependencies import require_admin

router = APIRouter()

class SystemSettingsUpdate(BaseModel):
    active_vtu_provider: Optional[str] = None
    vtu_api_key: Optional[str] = None
    vtu_secret_key: Optional[str] = None
    active_payment_gateway: Optional[str] = None
    monnify_api_key: Optional[str] = None
    monnify_secret_key: Optional[str] = None
    monnify_contract_code: Optional[str] = None
    paystack_secret_key: Optional[str] = None
    app_name: Optional[str] = None
    support_phone: Optional[str] = None
    support_whatsapp: Optional[str] = None
    announcement_banner: Optional[str] = None
    maintenance_mode: Optional[bool] = None

@router.get("/")
def get_settings(db: Session = Depends(get_db), admin=Depends(require_admin)):
    settings = db.query(SystemSettings).first()
    if not settings:
        settings = SystemSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings

@router.put("/")
def update_settings(payload: SystemSettingsUpdate, db: Session = Depends(get_db), admin=Depends(require_admin)):
    settings = db.query(SystemSettings).first()
    if not settings:
        settings = SystemSettings()
        db.add(settings)
    
    update_data = payload.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(settings, key, value)
        
    db.commit()
    db.refresh(settings)
    return settings
