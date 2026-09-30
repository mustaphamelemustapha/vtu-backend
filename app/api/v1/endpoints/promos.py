from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone

from app.core.database import get_db
from app.models.user import User
from app.models.promo import PromoCode, UserPromo
from app.schemas.promo import ClaimPromoRequest, UserPromoOut, PromoCodeOut, PromoCodeCreate
from app.dependencies import get_current_user, require_admin

router = APIRouter()

@router.post("/admin/create", response_model=PromoCodeOut)
def create_promo(
    promo_in: PromoCodeCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    """
    [Admin Only] Create a new promo code.
    """
    promo = db.query(PromoCode).filter(PromoCode.code == promo_in.code.upper()).first()
    if promo:
        raise HTTPException(status_code=400, detail="Promo code already exists.")
        
    new_promo = PromoCode(
        code=promo_in.code.upper(),
        description=promo_in.description,
        discount_amount=promo_in.discount_amount,
        is_percentage=promo_in.is_percentage,
        max_uses_per_user=promo_in.max_uses_per_user,
        max_total_uses=promo_in.max_total_uses,
        expires_at=promo_in.expires_at,
        is_active=promo_in.is_active,
    )
    db.add(new_promo)
    db.commit()
    db.refresh(new_promo)
    
    return new_promo

@router.get("/me", response_model=List[UserPromoOut])
def get_my_promos(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all promos claimed by the current user.
    """
    user_promos = db.query(UserPromo).filter(
        UserPromo.user_id == current_user.id
    ).order_by(UserPromo.created_at.desc()).all()
    
    return user_promos

@router.post("/claim", response_model=UserPromoOut)
def claim_promo(
    request: ClaimPromoRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Claim a promo code by code string.
    """
    code_str = request.code.strip().upper()
    
    promo = db.query(PromoCode).filter(PromoCode.code == code_str).first()
    if not promo:
        raise HTTPException(status_code=404, detail="Invalid promo code.")
        
    if not promo.is_active:
        raise HTTPException(status_code=400, detail="This promo code is inactive.")
        
    if promo.expires_at and promo.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="This promo code has expired.")
        
    if promo.max_total_uses and promo.current_uses >= promo.max_total_uses:
        raise HTTPException(status_code=400, detail="This promo code has reached its usage limit.")
        
    # Check if user already claimed it too many times
    user_claims = db.query(UserPromo).filter(
        UserPromo.user_id == current_user.id,
        UserPromo.promo_code_id == promo.id
    ).count()
    
    if user_claims >= promo.max_uses_per_user:
        raise HTTPException(status_code=400, detail="You have already claimed this promo code.")
        
    # Create UserPromo
    new_user_promo = UserPromo(
        user_id=current_user.id,
        promo_code_id=promo.id,
        status="READY"
    )
    
    # Increment total uses
    promo.current_uses += 1
    
    db.add(new_user_promo)
    db.commit()
    db.refresh(new_user_promo)
    
    return new_user_promo
