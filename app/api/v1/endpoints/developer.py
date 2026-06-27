import hashlib
import secrets
import time
import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.database import get_db
from app.models import User, UserRole, Transaction, TransactionStatus, TransactionType, DataPlan, ApiLog
from app.models.service_transaction import ServiceTransaction
from app.schemas.developer import (
    DeveloperStatusResponse,
    DeveloperApplyRequest,
    ApiKeyResponse,
    DeveloperWalletBalanceResponse,
    DeveloperDataPurchaseRequest,
    DeveloperAirtimePurchaseRequest,
    DeveloperPurchaseResponse
)
from app.services.wallet import get_or_create_wallet, debit_wallet, credit_wallet
from app.services.pricing import get_price_for_user
from app.services.fraud import enforce_purchase_limits

# Providers/Clients
from app.providers.smeplug_provider import SMEPlugProvider
from app.services.amigo import AmigoClient, AmigoApiError, resolve_network_id, normalize_plan_code
from app.services.bills import get_bills_provider
from app.dependencies import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)
security_bearer = HTTPBearer(auto_error=False)


# --- API Key Helpers ---
def generate_key_pair() -> tuple[str, str, str]:
    pub = f"MELE_PUB_{secrets.token_hex(16).upper()}"
    sec_plain = f"MELE_SEC_{secrets.token_hex(24).upper()}"
    sec_hash = hashlib.sha256(sec_plain.encode("utf-8")).hexdigest()
    return pub, sec_plain, sec_hash


# --- Developer Auth Dependency ---
def get_developer_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    db: Session = Depends(get_db)
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication credentials. Use 'Bearer <secret_key>' header.",
        )
    token = credentials.credentials.strip()
    if not token.startswith("MELE_SEC_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format.",
        )
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    user = db.query(User).filter(
        User.api_secret_key_hash == token_hash,
        User.developer_status == "approved",
        User.is_developer == True
    ).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed. Invalid developer API key or account suspended.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Developer account is inactive.",
        )
    return user


# --- Developer Management Endpoints (User Panel) ---

@router.get("/status", response_model=DeveloperStatusResponse)
def get_status(user: User = Depends(get_current_user)):
    return {
        "is_developer": user.is_developer,
        "developer_status": user.developer_status,
        "api_public_key": user.api_public_key,
        "has_keys": user.api_secret_key_hash is not None
    }


@router.post("/apply", response_model=DeveloperStatusResponse)
def apply_developer(payload: DeveloperApplyRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.developer_status not in {"none", ""}:
        raise HTTPException(status_code=400, detail=f"Developer application status is already: {user.developer_status}")
    
    pub, sec_plain, sec_hash = generate_key_pair()
    user.developer_status = "approved"
    user.is_developer = True
    user.role = UserRole.RESELLER
    user.api_public_key = pub
    user.api_secret_key_hash = sec_hash
    db.commit()
    db.refresh(user)
    return {
        "is_developer": user.is_developer,
        "developer_status": user.developer_status,
        "api_public_key": user.api_public_key,
        "has_keys": True,
        "api_secret_key": sec_plain
    }


@router.post("/keys/generate", response_model=ApiKeyResponse)
def generate_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.developer_status != "approved" or not user.is_developer:
        raise HTTPException(status_code=403, detail="Developer access has not been approved.")

    pub, sec_plain, sec_hash = generate_key_pair()
    user.api_public_key = pub
    user.api_secret_key_hash = sec_hash
    db.commit()
    return {
        "api_public_key": pub,
        "api_secret_key": sec_plain
    }


@router.post("/keys/revoke", response_model=DeveloperStatusResponse)
def revoke_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.api_public_key = None
    user.api_secret_key_hash = None
    db.commit()
    db.refresh(user)
    return {
        "is_developer": user.is_developer,
        "developer_status": user.developer_status,
        "api_public_key": user.api_public_key,
        "has_keys": False
    }


# --- Reseller Services API (Developer authenticated) ---

@router.get("/wallet/balance", response_model=DeveloperWalletBalanceResponse)
def get_balance(user: User = Depends(get_developer_user), db: Session = Depends(get_db)):
    wallet = get_or_create_wallet(db, user.id)
    return {"balance": wallet.balance, "currency": "NGN"}


@router.get("/data/plans")
def list_data_plans(user: User = Depends(get_developer_user), db: Session = Depends(get_db)):
    plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    result = []
    for plan in plans:
        price = get_price_for_user(db, plan, user)
        result.append({
            "plan_id": plan.id,
            "plan_code": plan.plan_code,
            "network": plan.network.upper(),
            "plan_name": plan.plan_name,
            "data_size": plan.data_size,
            "validity": plan.validity,
            "price": float(price)
        })
    return {"plans": result}


@router.post("/data/purchase", response_model=DeveloperPurchaseResponse)
def developer_buy_data(payload: DeveloperDataPurchaseRequest, user: User = Depends(get_developer_user), db: Session = Depends(get_db)):
    # 1. Idempotency Check
    client_ref = f"DEV_DATA_{payload.reference.strip()}"
    existing = db.query(Transaction).filter(Transaction.user_id == user.id, Transaction.reference == client_ref).first()
    if existing:
        return {
            "status": existing.status,
            "reference": existing.reference,
            "amount": existing.amount,
            "message": "Duplicate request detected. Transaction already processed."
        }

    # 2. Plan lookup
    network_key = payload.network.strip().lower()
    plan = db.query(DataPlan).filter(
        DataPlan.id == payload.plan_id,
        func.lower(DataPlan.network) == network_key,
        DataPlan.is_active == True
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Active data plan not found.")

    price = get_price_for_user(db, plan, user.role)
    wallet = get_or_create_wallet(db, user.id)
    if wallet.balance < price:
        raise HTTPException(status_code=400, detail="Insufficient wallet balance.")

    # 3. Debit Wallet
    try:
        debit_wallet(db, wallet, price, client_ref, f"API Data: {plan.plan_name} ({payload.phone_number})")
    except Exception as e:
        logger.error(f"Developer wallet debit failed: {e}")
        raise HTTPException(status_code=400, detail="Wallet debit failed.")

    # 4. Save transaction
    tx = Transaction(
        user_id=user.id,
        amount=price,
        tx_type=TransactionType.DATA,
        status=TransactionStatus.PENDING,
        reference=client_ref,
        network=plan.network,
        recipient_phone=payload.phone_number.strip(),
        data_plan_code=plan.plan_code,
        provider=plan.provider,
        provider_plan_id=plan.provider_plan_id
    )
    db.add(tx)
    db.commit()

    # 5. Route to Provider
    provider_res = {"status": "pending", "error": "Provider routing failed"}
    start_time = time.time()
    try:
        provider_name = str(plan.provider or "").strip().lower()
        phone = payload.phone_number.strip()
        if provider_name in ("smeplug", "sim"):
            sme = SMEPlugProvider()
            sme_network_map = {"mtn": 1, "airtel": 2, "9mobile": 3, "glo": 4}
            net_id = sme_network_map.get(network_key, 2)
            provider_res = sme.purchase_network_data(net_id, phone, plan.provider_plan_id or plan.plan_code, client_ref)
        elif provider_name == "amigo" or (not provider_name and network_key in {"mtn", "glo", "airtel", "9mobile"}):
            amigo = AmigoClient()
            amigo_payload = {
                "network": resolve_network_id(network_key),
                "mobile_number": phone,
                "plan": normalize_plan_code(plan.plan_code),
                "Ported_number": True
            }
            res = amigo.purchase_data(amigo_payload, idempotency_key=client_ref)
            if res.get("success") or str(res.get("status")).lower() in {"delivered", "success", "successful"}:
                provider_res = {"status": "success", "provider_reference": str(res.get("reference") or "")}
            elif str(res.get("status")).lower() in {"pending", "processing"}:
                provider_res = {"status": "pending", "provider_reference": str(res.get("reference") or "")}
            else:
                provider_res = {"status": "failed", "error": res.get("message") or "Amigo reported failure"}
        elif provider_name == "clubkonnect" or (not provider_name and network_key == "9mobile"):
            bills = get_bills_provider()
            res = bills.purchase_data(network_key, phone, plan.provider_plan_id or plan.plan_code, amount=float(price), request_id=client_ref)
            if res.ok:
                provider_res = {"status": "success", "provider_reference": res.external_reference}
            elif res.is_pending:
                provider_res = {"status": "pending", "provider_reference": res.external_reference}
            else:
                provider_res = {"status": "failed", "error": res.message}
    except Exception as exc:
        logger.error("Developer purchase exception: %s", exc)
        provider_res = {"status": "pending", "error": str(exc)}

    duration_ms = (time.time() - start_time) * 1000

    # 6. Update Transaction Status
    final_status = provider_res.get("status", "pending")
    tx.status = TransactionStatus.SUCCESS if final_status == "success" else (TransactionStatus.FAILED if final_status == "failed" else TransactionStatus.PENDING)
    tx.external_reference = provider_res.get("provider_reference")
    
    if final_status == "failed":
        tx.failure_reason = str(provider_res.get("error"))[:255]
        credit_wallet(db, wallet, price, client_ref, f"Refund: {plan.plan_name} API purchase failed")
        tx.status = TransactionStatus.REFUNDED
    db.commit()

    # 7. Write Log
    api_log = ApiLog(
        user_id=user.id,
        service=tx.provider or "data_api",
        endpoint="/developer/data/purchase",
        status_code=200,
        duration_ms=Decimal(str(round(duration_ms, 2))),
        reference=client_ref,
        success=1 if final_status == "success" else 0
    )
    db.add(api_log)
    db.commit()

    return {
        "status": final_status,
        "reference": client_ref,
        "amount": price,
        "message": provider_res.get("error") if final_status == "failed" else "Transaction successful" if final_status == "success" else "Processing"
    }


@router.post("/airtime/purchase", response_model=DeveloperPurchaseResponse)
def developer_buy_airtime(payload: DeveloperAirtimePurchaseRequest, user: User = Depends(get_developer_user), db: Session = Depends(get_db)):
    client_ref = f"DEV_AIRTIME_{payload.reference.strip()}"
    existing = db.query(ServiceTransaction).filter(ServiceTransaction.user_id == user.id, ServiceTransaction.reference == client_ref).first()
    if existing:
        return {
            "status": existing.status,
            "reference": existing.reference,
            "amount": existing.amount,
            "message": "Duplicate request detected. Transaction already processed."
        }

    base_amount = payload.amount
    # Resolve discount
    from app.api.v1.endpoints.services import get_service_charge_for_user
    charge_amount, margin = get_service_charge_for_user(
        db,
        tx_type=TransactionType.AIRTIME.value,
        provider=payload.network.strip().lower(),
        base_amount=base_amount,
        user_role=user.role,
    )
    if charge_amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid final purchase amount.")

    wallet = get_or_create_wallet(db, user.id)
    if wallet.balance < charge_amount:
        raise HTTPException(status_code=400, detail="Insufficient wallet balance.")

    # 1. Debit Wallet
    try:
        debit_wallet(db, wallet, charge_amount, client_ref, f"API Airtime: ₦{base_amount} ({payload.phone_number})")
    except Exception as e:
        logger.error(f"Developer airtime debit failed: {e}")
        raise HTTPException(status_code=400, detail="Wallet debit failed.")

    # 2. Save Transaction
    tx = ServiceTransaction(
        user_id=user.id,
        reference=client_ref,
        tx_type=TransactionType.AIRTIME.value,
        amount=charge_amount,
        status=TransactionStatus.PENDING.value,
        provider=payload.network.strip().lower(),
        customer=payload.phone_number.strip(),
        meta={
            "network": payload.network.strip().lower(),
            "phone_number": payload.phone_number.strip(),
            "base_amount": str(base_amount),
            "margin_applied": str(margin),
            "charge_amount": str(charge_amount),
        },
    )
    db.add(tx)
    db.commit()

    # 3. Route to Provider
    start_time = time.time()
    provider_res = {"status": "pending", "error": "Provider confirmation pending"}
    try:
        provider = get_bills_provider()
        result = provider.purchase_airtime(payload.network.strip().lower(), payload.phone_number.strip(), float(base_amount))
        
        if result.success:
            provider_res = {"status": "success", "provider_reference": result.external_reference}
        else:
            provider_res = {"status": "failed", "error": result.message or "Provider rejected airtime"}
    except Exception as exc:
        logger.error("Developer airtime provider exception: %s", exc)
        provider_res = {"status": "pending", "error": str(exc)}

    duration_ms = (time.time() - start_time) * 1000

    # 4. Handle Result
    final_status = provider_res.get("status", "pending")
    tx.status = TransactionStatus.SUCCESS.value if final_status == "success" else (TransactionStatus.FAILED.value if final_status == "failed" else TransactionStatus.PENDING.value)
    tx.external_reference = provider_res.get("provider_reference")

    if final_status == "failed":
        tx.failure_reason = str(provider_res.get("error"))[:255]
        credit_wallet(db, wallet, charge_amount, client_ref, "API Refund: Airtime purchase failed")
        tx.status = TransactionStatus.REFUNDED.value
    db.commit()

    # 5. Write Log
    api_log = ApiLog(
        user_id=user.id,
        service="airtime_api",
        endpoint="/developer/airtime/purchase",
        status_code=200,
        duration_ms=Decimal(str(round(duration_ms, 2))),
        reference=client_ref,
        success=1 if final_status == "success" else 0
    )
    db.add(api_log)
    db.commit()

    return {
        "status": final_status,
        "reference": client_ref,
        "amount": charge_amount,
        "message": provider_res.get("error") if final_status == "failed" else "Transaction successful" if final_status == "success" else "Processing"
    }
