from fastapi import APIRouter, Depends, HTTPException, Request
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
    maintenance_message: Optional[str] = None
    min_app_version: Optional[str] = None
    latest_app_version: Optional[str] = None
    play_store_url: Optional[str] = None
    app_store_url: Optional[str] = None
    release_notes: Optional[str] = None

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


class CommissionSettingsUpdate(BaseModel):
    ambassador_deposit_percent: Optional[float] = None
    agent_data_commission_percent: Optional[float] = None
    enable_agent_data_commission: Optional[bool] = None
    enable_welcome_data_bonus: Optional[bool] = None
    welcome_data_bonus_amount: Optional[float] = None
    welcome_data_bonus_min_gb: Optional[float] = None


@router.get("/commissions")
def get_commissions(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    from app.services.commissions import get_brand_commission_config
    return get_brand_commission_config(db, request=request, admin=admin)


@router.put("/commissions")
def update_commissions(
    payload: CommissionSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    from app.services.commissions import update_brand_commission_config
    return update_brand_commission_config(
        db,
        ambassador_deposit_percent=payload.ambassador_deposit_percent,
        agent_data_commission_percent=payload.agent_data_commission_percent,
        enable_agent_data_commission=payload.enable_agent_data_commission,
        enable_welcome_data_bonus=payload.enable_welcome_data_bonus,
        welcome_data_bonus_amount=payload.welcome_data_bonus_amount,
        welcome_data_bonus_min_gb=payload.welcome_data_bonus_min_gb,
        request=request,
        admin=admin,
    )


@router.post("/commissions/reconcile-agents")
def reconcile_commissions(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    from app.services.commissions import reconcile_missing_agent_commissions
    reconciled = reconcile_missing_agent_commissions(db, request=request, admin=admin)
    return {"status": "ok", "count": len(reconciled), "items": reconciled}


@router.post("/commissions/reconcile-welcome-bonus")
def reconcile_welcome_bonuses(
    request: Request,
    limit: int = 500,
    brand: Optional[str] = "kullomadata",
    all_users: bool = False,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    from app.services.commissions import reconcile_missing_welcome_bonuses
    reconciled = reconcile_missing_welcome_bonuses(
        db,
        request=request,
        admin=admin,
        brand=brand or "kullomadata",
        limit=limit,
        all_users=all_users,
    )
    return {"status": "ok", "count": len(reconciled), "items": reconciled}


@router.post("/commissions/purge-welcome-bonuses")
def purge_welcome_bonuses(
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    from app.services.commissions import purge_accidental_welcome_bonuses
    return purge_accidental_welcome_bonuses(db, brand="meledata")

