from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.dependencies import require_admin
from app.models import User, Transaction, TransactionStatus, PricingRule, PricingRole, ApiLog
from app.schemas.admin import FundUserWalletRequest, PricingRuleUpdate
from app.services.wallet import get_or_create_wallet, credit_wallet

router = APIRouter()


@router.get("/analytics")

def analytics(admin=Depends(require_admin), db: Session = Depends(get_db)):
    total_revenue = db.query(func.sum(Transaction.amount)).filter(Transaction.status == TransactionStatus.SUCCESS).scalar() or 0
    total_users = db.query(func.count(User.id)).scalar() or 0
    daily_tx = db.query(func.count(Transaction.id)).filter(Transaction.status == TransactionStatus.SUCCESS).scalar() or 0
    api_success = db.query(func.count(ApiLog.id)).filter(ApiLog.success == 1).scalar() or 0
    api_failed = db.query(func.count(ApiLog.id)).filter(ApiLog.success == 0).scalar() or 0
    return {
        "total_revenue": total_revenue,
        "total_users": total_users,
        "daily_transactions": daily_tx,
        "api_success": api_success,
        "api_failed": api_failed,
    }


@router.post("/fund-wallet")

def fund_user_wallet(payload: FundUserWalletRequest, admin=Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    wallet = get_or_create_wallet(db, user.id)
    credit_wallet(db, wallet, payload.amount, f"ADMIN_{user.id}", payload.description)
    return {"status": "ok"}


@router.post("/pricing")

def update_pricing(payload: PricingRuleUpdate, admin=Depends(require_admin), db: Session = Depends(get_db)):
    role = PricingRole.USER if payload.role == "user" else PricingRole.RESELLER
    rule = db.query(PricingRule).filter(PricingRule.network == payload.network, PricingRule.role == role).first()
    if not rule:
        rule = PricingRule(network=payload.network, role=role, margin=payload.margin)
        db.add(rule)
    else:
        rule.margin = payload.margin
    db.commit()
    return {"status": "ok"}


@router.post("/users/{user_id}/suspend")

def suspend_user(user_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.commit()
    return {"status": "suspended"}


@router.post("/users/{user_id}/activate")

def activate_user(user_id: int, admin=Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = True
    db.commit()
    return {"status": "active"}
