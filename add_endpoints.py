import sys

endpoints = """
from app.models.agent import AgentStat

@router.get("/agents", response_model=AdminAgentsResponse)
def list_agents(
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    query = db.query(User).filter(User.role == UserRole.AGENT)
    total = query.count()
    users = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for user in users:
        wallet = get_or_create_wallet(db, user.id)
        stat = db.query(AgentStat).filter(AgentStat.agent_id == user.id).first()
        cum_mb = stat.total_data_mb if stat else 0
        
        items.append({
            "id": user.id,
            "name": user.full_name or "Unknown",
            "email": user.email,
            "phone": user.phone_number,
            "wallet_balance": wallet.balance,
            "cumulative_sales_gb": Decimal(cum_mb) / Decimal(1024),
            "upgraded_at": user.updated_at,
        })

    return AdminAgentsResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/ambassadors", response_model=AdminAmbassadorsResponse)
def list_ambassadors(
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    query = db.query(User).filter(User.role == UserRole.AMBASSADOR)
    total = query.count()
    ambassadors = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for amb in ambassadors:
        referrals = db.query(Referral).filter(Referral.referrer_id == amb.id).all()
        
        vendors = []
        for ref in referrals:
            vendor = db.query(User).filter(User.id == ref.referred_id).first()
            if vendor:
                vendors.append({
                    "vendor_id": vendor.id,
                    "vendor_name": vendor.full_name or "Unknown",
                    "vendor_email": vendor.email,
                    "initial_deposit_amount": ref.first_deposit_amount,
                    "is_ten_percent_paid": ref.is_ten_percent_paid,
                    "accumulated_gb": Decimal(ref.accumulated_mb) / Decimal(1024),
                    "is_50gb_milestone_reached": ref.is_50gb_milestone_reached,
                    "is_milestone_bonus_paid": ref.is_milestone_bonus_paid,
                })
        
        items.append({
            "id": amb.id,
            "name": amb.full_name or "Unknown",
            "email": amb.email,
            "phone": amb.phone_number,
            "total_vendors_onboarded": len(vendors),
            "vendors": vendors
        })

    return AdminAmbassadorsResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/ambassadors/pay-commission")
def pay_ambassador_commission(
    req: PayCommissionRequest,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    ambassador = db.query(User).filter(User.id == req.ambassador_id, User.role == UserRole.AMBASSADOR).first()
    if not ambassador:
        raise HTTPException(status_code=404, detail="Ambassador not found")
        
    referral = db.query(Referral).filter(
        Referral.referrer_id == req.ambassador_id,
        Referral.referred_id == req.vendor_id
    ).first()
    
    if not referral:
        raise HTTPException(status_code=404, detail="Referral not found")
        
    wallet = get_or_create_wallet(db, ambassador.id)
    
    if req.commission_type == "10_PERCENT":
        if referral.is_ten_percent_paid:
            raise HTTPException(status_code=400, detail="Commission already paid")
        if not referral.first_deposit_amount:
            raise HTTPException(status_code=400, detail="Vendor has not made a deposit yet")
            
        amount = Decimal(referral.first_deposit_amount) * Decimal('0.10')
        credit_wallet(db, wallet.id, amount, "Ambassador 10% Onboarding Commission")
        
        referral.is_ten_percent_paid = True
        
    elif req.commission_type == "50GB_MILESTONE":
        if referral.is_milestone_bonus_paid:
            raise HTTPException(status_code=400, detail="Milestone bonus already paid")
        if not referral.is_50gb_milestone_reached:
            raise HTTPException(status_code=400, detail="Milestone not reached yet")
            
        amount = Decimal('500.00')
        credit_wallet(db, wallet.id, amount, "Ambassador 50GB Milestone Bonus")
        
        referral.is_milestone_bonus_paid = True
        
    else:
        raise HTTPException(status_code=400, detail="Invalid commission type")
        
    db.commit()
    return {"status": "success", "message": f"Paid {amount} commission to Ambassador."}
"""

with open("app/api/v1/endpoints/admin.py", "a") as f:
    f.write(endpoints)
