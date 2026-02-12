import secrets
import time
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings
from app.dependencies import get_current_user, require_admin
from app.models import User, DataPlan, Transaction, TransactionStatus, TransactionType, ApiLog
from app.schemas.data import DataPlanOut, BuyDataRequest
from app.services.amigo import AmigoClient
from app.services.wallet import get_or_create_wallet, debit_wallet, credit_wallet
from app.services.pricing import get_price_for_user
from app.middlewares.rate_limit import limiter
from app.utils.cache import get_cached, set_cached

router = APIRouter()
settings = get_settings()


@router.get("/plans", response_model=list[DataPlanOut])

def list_data_plans(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cache_key = f"plans:{user.role.value}"
    cached = get_cached(cache_key)
    if cached:
        return cached

    plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    if not plans:
        # Auto-seed from Amigo catalog if DB is empty
        client = AmigoClient()
        response = client.fetch_data_plans()
        items = response.get("data", [])
        for item in items:
            plan = DataPlan(
                network=item.get("network"),
                plan_code=str(item.get("plan_code")),
                plan_name=item.get("plan_name"),
                data_size=item.get("data_size"),
                validity=item.get("validity"),
                base_price=Decimal(str(item.get("price", 0))),
                is_active=True,
            )
            db.add(plan)
        db.commit()
        plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    priced = []
    for plan in plans:
        price = get_price_for_user(db, plan, user.role)
        priced.append(
            DataPlanOut(
                id=plan.id,
                network=plan.network,
                plan_code=plan.plan_code,
                plan_name=plan.plan_name,
                data_size=plan.data_size,
                validity=plan.validity,
                price=price,
            )
        )
    set_cached(cache_key, priced, ttl_seconds=60)
    return priced


@router.post("/purchase")
@limiter.limit("5/minute")
def buy_data(request: Request, payload: BuyDataRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = db.query(DataPlan).filter(DataPlan.plan_code == payload.plan_code, DataPlan.is_active == True).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    price = get_price_for_user(db, plan, user.role)
    wallet = get_or_create_wallet(db, user.id)

    reference = f"DATA_{secrets.token_hex(8)}"
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        network=plan.network,
        data_plan_code=plan.plan_code,
        amount=price,
        status=TransactionStatus.PENDING,
        tx_type=TransactionType.DATA,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    debit_wallet(db, wallet, Decimal(price), reference, "Data purchase")

    client = AmigoClient()
    start = time.time()
    try:
        network_id = 1 if plan.network == "mtn" else 2 if plan.network == "glo" else 0
        response = client.purchase_data({
            "network": network_id,
            "mobile_number": payload.phone_number,
            "plan": int(plan.plan_code),
            "Ported_number": payload.ported_number,
        })
        duration_ms = round((time.time() - start) * 1000, 2)
        db.add(ApiLog(
            user_id=user.id,
            service="amigo",
            endpoint="/data/purchase",
            status_code=200,
            duration_ms=duration_ms,
            reference=reference,
            success=1,
        ))

        success = response.get("success") is True
        status = (response.get("status") or "").lower()
        if success and status in {"delivered", "success"}:
            transaction.status = TransactionStatus.SUCCESS
            transaction.external_reference = response.get("reference")
        elif not success:
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = response.get("message", "Amigo failed")
            credit_wallet(db, wallet, Decimal(price), reference, "Auto refund for failed data purchase")
            transaction.status = TransactionStatus.REFUNDED
        else:
            transaction.status = TransactionStatus.PENDING

        db.commit()
        return {
            "reference": reference,
            "status": transaction.status,
            "test_mode": settings.amigo_test_mode,
        }
    except Exception as exc:
        duration_ms = round((time.time() - start) * 1000, 2)
        db.add(ApiLog(
            user_id=user.id,
            service="amigo",
            endpoint="/data/purchase",
            status_code=500,
            duration_ms=duration_ms,
            reference=reference,
            success=0,
        ))
        transaction.status = TransactionStatus.FAILED
        transaction.failure_reason = str(exc)
        credit_wallet(db, wallet, Decimal(price), reference, "Auto refund due to Amigo error")
        transaction.status = TransactionStatus.REFUNDED
        db.commit()
        raise HTTPException(status_code=502, detail="Amigo API error")


@router.post("/sync")
def sync_data_plans(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    client = AmigoClient()
    response = client.fetch_data_plans()
    plans = response.get("data", [])
    updated = 0
    for item in plans:
        plan = db.query(DataPlan).filter(DataPlan.plan_code == item.get("plan_code")).first()
        if not plan:
            plan = DataPlan(
                network=item.get("network"),
                plan_code=item.get("plan_code"),
                plan_name=item.get("plan_name"),
                data_size=item.get("data_size"),
                validity=item.get("validity"),
                base_price=Decimal(str(item.get("price", 0))),
                is_active=True,
            )
            db.add(plan)
        else:
            plan.plan_name = item.get("plan_name", plan.plan_name)
            plan.data_size = item.get("data_size", plan.data_size)
            plan.validity = item.get("validity", plan.validity)
            plan.base_price = Decimal(str(item.get("price", plan.base_price)))
            plan.is_active = True
        updated += 1
    db.commit()
    return {"updated": updated}
