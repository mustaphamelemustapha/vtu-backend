import hashlib
import logging
import re
import secrets
import time
from decimal import Decimal
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import get_settings
from app.dependencies import get_current_user, require_admin
from app.models import User, UserRole, DataPlan, Transaction, TransactionStatus, TransactionType, ApiLog
from app.schemas.data import DataPlanOut, BuyDataRequest
from app.services.amigo import (
    AmigoClient,
    AmigoApiError,
    canonical_plan_code,
    normalize_plan_code,
    resolve_network_id,
    split_plan_code,
)
from app.providers.smeplug_provider import SMEPlugProvider
from app.services.bills import get_bills_provider
from app.services.fraud import enforce_purchase_limits
from app.services.wallet import get_or_create_wallet, debit_wallet, credit_wallet
from app.services.pricing import get_price_for_user
from app.middlewares.rate_limit import limiter

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

# --- HELPERS ---

def _invalidate_plans_cache():
    """Placeholder for legacy cache invalidation."""
    pass

def _parse_size_gb(size_str: str | None) -> float:
    if not size_str:
        return 0.0
    try:
        s = str(size_str).strip().upper()
        match = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB)", s)
        if not match:
            return 0.0
        val = float(match.group(1))
        unit = match.group(2)
        return val if unit == "GB" else val / 1024
    except Exception:
        return 0.0

def _clean_plan_label(name: str | None) -> str:
    if not name:
        return ""
    return str(name).replace("(Direct Data)", "").replace("Direct Data", "").strip()

def _promo_plan_code_suffix(plan_code: str | None) -> str:
    raw = str(plan_code or "").strip().lower()
    if ":" in raw:
        return raw.split(":")[-1]
    return raw

def _is_mtn_1gb_promo_plan(plan: DataPlan) -> bool:
    if not getattr(settings, "promo_mtn_1gb_enabled", False):
        return False
    network = str(plan.network or "").strip().lower()
    promo_nw = str(getattr(settings, "promo_mtn_1gb_network", "mtn")).strip().lower()
    if network != promo_nw:
        return False
    suffix = _promo_plan_code_suffix(plan.plan_code)
    promo_code = str(getattr(settings, "promo_mtn_1gb_plan_code", "1001")).strip().lower()
    return suffix == promo_code

def _count_mtn_1gb_promo_successes(db: Session) -> int:
    network = str(getattr(settings, "promo_mtn_1gb_network", "mtn")).strip().lower()
    promo_price = Decimal(str(getattr(settings, "promo_mtn_1gb_price", "199")))
    count = (
        db.query(func.count(func.distinct(Transaction.user_id)))
        .filter(
            Transaction.tx_type == TransactionType.DATA,
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.amount == promo_price,
            func.lower(Transaction.network) == network,
        )
        .scalar()
    )
    return int(count or 0)

def _mtn_1gb_promo_snapshot(db: Session) -> dict:
    limit = max(int(getattr(settings, "promo_mtn_1gb_limit", 0)), 0)
    enabled = bool(getattr(settings, "promo_mtn_1gb_enabled", False))
    if not enabled or limit <= 0:
        return {"active": False, "remaining": 0, "limit": limit, "price": Decimal("250")}
    
    consumed = _count_mtn_1gb_promo_successes(db)
    remaining = max(limit - consumed, 0)
    return {
        "active": remaining > 0,
        "remaining": remaining,
        "limit": limit,
        "price": Decimal(str(getattr(settings, "promo_mtn_1gb_price", "199")))
    }

def _user_has_used_mtn_1gb_promo(db: Session, user_id: int) -> bool:
    network = str(getattr(settings, "promo_mtn_1gb_network", "mtn")).strip().lower()
    promo_price = Decimal(str(getattr(settings, "promo_mtn_1gb_price", "199")))
    row = (
        db.query(Transaction.id)
        .filter(
            Transaction.user_id == int(user_id),
            Transaction.tx_type == TransactionType.DATA,
            Transaction.status == TransactionStatus.SUCCESS,
            Transaction.amount == promo_price,
            func.lower(Transaction.network) == network,
        )
        .first()
    )
    return bool(row)

def _safe_reason(value: str, limit: int = 255) -> str:
    text = str(value or "").strip()
    return text[:limit] if text else "Unknown provider error"

def _is_ambiguous_provider_error(exc: Exception) -> bool:
    msg = str(exc).strip().lower()
    ambiguous_hints = (
        "timeout", "timed out", "connection error", "connection reset", 
        "non-json", "invalid json", "service unavailable", "remote protocol"
    )
    return any(hint in msg for hint in ambiguous_hints)

def _upsert_plan_from_provider(db: Session, item: dict) -> bool:
    network = str(item.get("network") or "").lower()
    plan_code = str(item.get("plan_code") or "").strip()
    if not network or not plan_code:
        return False

    canonical_code = canonical_plan_code(network, plan_code)
    clean_plan_name = _clean_plan_label(item.get("plan_name"))
    clean_data_size = item.get("data_size")
    clean_validity = item.get("validity")
    clean_provider = str(item.get("provider") or "").lower()
    clean_provider_plan_id = str(item.get("provider_plan_id") or "")

    plan = db.query(DataPlan).filter(DataPlan.plan_code == canonical_code).first()

    if not plan:
        plan = DataPlan(
            network=network,
            plan_code=canonical_code,
            plan_name=clean_plan_name,
            data_size=clean_data_size,
            validity=clean_validity,
            base_price=Decimal(str(item.get("price") or "0")),
            provider=clean_provider,
            provider_plan_id=clean_provider_plan_id,
            is_active=True,
        )
        db.add(plan)
        return True

    plan.network = network
    plan.plan_name = clean_plan_name or plan.plan_name
    plan.data_size = clean_data_size or plan.data_size
    plan.validity = clean_validity or plan.validity
    plan.base_price = Decimal(str(item.get("price") or plan.base_price))
    plan.provider = clean_provider or plan.provider
    plan.provider_plan_id = clean_provider_plan_id or plan.provider_plan_id
    return True

# --- ENDPOINTS ---

@router.get("/plans", response_model=list[DataPlanOut])
def list_data_plans(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    
    breakdown = {}
    for p in plans:
        nw = str(p.network or "").lower()
        breakdown[nw] = breakdown.get(nw, 0) + 1

    # 1. AIRTEL -> SMEPlug Sync (If low)
    if breakdown.get("airtel", 0) < 5:
        logger.info("Airtel plans low. Syncing from SMEPlug...")
        try:
            sme = SMEPlugProvider()
            items = sme.get_airtel_plans()
            if items:
                touched = 0
                for item in items:
                    item["provider"] = "smeplug"
                    touched += 1 if _upsert_plan_from_provider(db, item) else 0
                if touched:
                    db.commit()
                    plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
                    logger.info("SMEPlug sync finished (Airtel). Touched %d plans.", touched)
        except Exception as exc:
            logger.warning("SMEPlug Airtel sync failed: %s", exc)

    # 2. 9MOBILE -> ClubKonnect/Bills Sync (If low)
    if breakdown.get("9mobile", 0) < 5:
        logger.info("9mobile plans low. Syncing from Bills Provider...")
        try:
            provider = get_bills_provider()
            if hasattr(provider, "fetch_data_variations"):
                items = provider.fetch_data_variations("9mobile")
                if items:
                    touched = 0
                    for item in items:
                        item["network"] = "9mobile"
                        item["provider"] = str(getattr(provider, "name", "clubkonnect")).lower()
                        touched += 1 if _upsert_plan_from_provider(db, item) else 0
                    if touched:
                        db.commit()
                        plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
                        logger.info("9mobile sync finished. Touched %d plans.", touched)
        except Exception as exc:
            logger.warning("9mobile sync failed: %s", exc)

    # 3. MTN/GLO -> Amigo Sync (If low)
    if breakdown.get("mtn", 0) < 5 or breakdown.get("glo", 0) < 5:
        logger.info("MTN or Glo plans low. Syncing from Amigo...")
        try:
            amigo = AmigoClient()
            res = amigo.fetch_data_plans()
            items = res.get("data", [])
            if items:
                touched = 0
                for item in items:
                    touched += 1 if _upsert_plan_from_provider(db, item) else 0
                if touched:
                    db.commit()
                    plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
                    logger.info("Amigo sync finished. Touched %d plans.", touched)
        except Exception as exc:
            logger.warning("Amigo sync failed: %s", exc)

    promo_snapshot = _mtn_1gb_promo_snapshot(db)
    user_promo_used = _user_has_used_mtn_1gb_promo(db, user.id)
    
    priced = []
    for plan in plans:
        price = get_price_for_user(db, plan, user.role)
        promo_active = False
        promo_old_price = None
        promo_label = None
        promo_remaining = None
        promo_limit = None
        
        if _is_mtn_1gb_promo_plan(plan):
            promo_limit = int(promo_snapshot["limit"])
            promo_remaining = int(promo_snapshot["remaining"])
            promo_active = bool(promo_snapshot["active"]) and not user_promo_used
            if promo_active:
                promo_price = Decimal(str(promo_snapshot["price"]))
                if promo_price < price:
                    promo_old_price = price
                    price = promo_price
                    promo_label = "First 50 promo"

        priced.append(
            DataPlanOut(
                id=plan.id,
                network=plan.network,
                plan_code=plan.plan_code,
                plan_name=_clean_plan_label(plan.plan_name),
                data_size=plan.data_size,
                validity=plan.validity,
                price=price,
                base_price=plan.base_price,
                promo_active=promo_active,
                promo_old_price=promo_old_price,
                promo_label=promo_label,
                promo_remaining=promo_remaining,
                promo_limit=promo_limit,
                provider=plan.provider,
                provider_plan_id=plan.provider_plan_id,
            )
        )
    
    priced.sort(key=lambda p: (str(p.network or "").lower(), p.price or 0, _parse_size_gb(p.data_size or p.plan_name) or 0))
    return priced


@router.post("/purchase")
@limiter.limit("5/minute")
def buy_data(request: Request, payload: BuyDataRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan_code_input = str(payload.plan_code or "").strip()
    payload_network = str(payload.network or "").strip().lower()
    
    plan_query = db.query(DataPlan).filter(DataPlan.plan_code == plan_code_input, DataPlan.is_active == True)
    if payload_network:
        plan_query = plan_query.filter(func.lower(DataPlan.network) == payload_network)
    plan = plan_query.first()
    
    if not plan and ":" not in plan_code_input and plan_code_input:
        suffix_query = (
            db.query(DataPlan)
            .filter(DataPlan.plan_code.like(f"%:{plan_code_input}"), DataPlan.is_active == True)
        )
        if payload_network:
            suffix_query = suffix_query.filter(func.lower(DataPlan.network) == payload_network)
        suffix_matches = suffix_query.all()
        if len(suffix_matches) == 1:
            plan = suffix_matches[0]
            
    if not plan:
        raise HTTPException(status_code=404, detail="Active data plan not found.")

    phone = str(payload.phone_number or "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Recipient phone number is required.")

    enforce_purchase_limits(db, user, "data")
    
    price = get_price_for_user(db, plan, user.role)
    if _is_mtn_1gb_promo_plan(plan):
        promo = _mtn_1gb_promo_snapshot(db)
        if promo["active"] and not _user_has_used_mtn_1gb_promo(db, user.id):
            promo_price = Decimal(str(promo["price"]))
            if promo_price < price:
                price = promo_price

    if user.balance < price:
        raise HTTPException(status_code=400, detail="Insufficient wallet balance.")

    reference = f"DATA-{int(time.time())}-{secrets.token_hex(4)}".upper()
    
    # 1. DEBIT WALLET
    if not debit_wallet(db, user.id, price, f"Data Purchase: {plan.plan_name} ({phone})", reference):
        raise HTTPException(status_code=400, detail="Wallet debit failed.")

    # 2. CREATE PENDING TRANSACTION
    transaction = Transaction(
        user_id=user.id,
        amount=price,
        tx_type=TransactionType.DATA,
        status=TransactionStatus.PENDING,
        reference=reference,
        network=plan.network,
        recipient_phone=phone,
        data_plan_code=plan.plan_code,
        provider=plan.provider,
        provider_plan_id=plan.provider_plan_id
    )
    db.add(transaction)
    db.commit()

    # 3. ROUTE TO PROVIDER
    provider_res = {"status": "pending", "error": "Provider routing failed"}
    network_key = str(plan.network or "").lower()
    
    start_time = time.time()
    try:
        # AIRTEL -> SMEPlug
        if network_key == "airtel":
            sme = SMEPlugProvider()
            provider_res = sme.purchase_airtel_data(phone, plan.provider_plan_id or plan.plan_code, reference)
            transaction.provider = "smeplug"
            
        # MTN/GLO -> Amigo
        elif network_key in {"mtn", "glo"}:
            amigo = AmigoClient()
            amigo_network_id = 1 if network_key == "mtn" else 3
            amigo_payload = {
                "network": amigo_network_id,
                "mobile_number": phone,
                "plan": normalize_plan_code(plan.plan_code),
                "Ported_number": True
            }
            transaction.provider = "amigo"
            try:
                res = amigo.purchase_data(amigo_payload, idempotency_key=reference)
                if res.get("success") or str(res.get("status")).lower() in {"delivered", "success", "successful"}:
                    provider_res = {"status": "success", "provider_reference": str(res.get("reference") or "")}
                elif str(res.get("status")).lower() in {"pending", "processing"}:
                    provider_res = {"status": "pending", "provider_reference": str(res.get("reference") or "")}
                else:
                    provider_res = {"status": "failed", "error": res.get("message") or "Amigo reported failure"}
            except AmigoApiError as e:
                provider_res = {"status": "failed", "error": str(e)}

        # 9MOBILE -> ClubKonnect/Bills
        elif network_key == "9mobile":
            bills = get_bills_provider()
            transaction.provider = "clubkonnect"
            res = bills.purchase_data(network_key, phone, plan.provider_plan_id or plan.plan_code, amount=float(price), request_id=reference)
            if res.ok:
                provider_res = {"status": "success", "provider_reference": res.external_reference}
            elif res.is_pending:
                provider_res = {"status": "pending", "provider_reference": res.external_reference}
            else:
                provider_res = {"status": "failed", "error": res.message}
        
        else:
            provider_res = {"status": "failed", "error": f"No provider configured for network: {network_key}"}

    except Exception as exc:
        logger.error("Data purchase provider exception: %s", exc)
        if _is_ambiguous_provider_error(exc):
            provider_res = {"status": "pending", "error": f"Provider timeout/error: {str(exc)}"}
        else:
            provider_res = {"status": "failed", "error": str(exc)}

    duration_ms = (time.time() - start_time) * 1000
    
    # 4. HANDLE RESULT
    final_status = provider_res.get("status", "pending")
    transaction.status = TransactionStatus.SUCCESS if final_status == "success" else (TransactionStatus.FAILED if final_status == "failed" else TransactionStatus.PENDING)
    transaction.external_reference = provider_res.get("provider_reference")
    
    if final_status == "failed":
        transaction.failure_reason = _safe_reason(provider_res.get("error"))
        credit_wallet(db, user.id, price, f"Refund: {plan.plan_name} purchase failed", reference)
        transaction.status = TransactionStatus.REFUNDED

    db.commit()

    # Log API call (Using correct ApiLog fields: user_id, service, endpoint, status_code, duration_ms, reference, success)
    api_log = ApiLog(
        user_id=user.id,
        service=transaction.provider or "data",
        endpoint="/data/purchase",
        status_code=200,
        duration_ms=duration_ms,
        reference=reference,
        success=1 if final_status == "success" else 0
    )
    db.add(api_log)
    db.commit()

    return {
        "status": final_status,
        "message": provider_res.get("error") if final_status == "failed" else "Transaction successful" if final_status == "success" else "Transaction is processing",
        "reference": reference,
        "provider_reference": transaction.external_reference
    }


@router.post("/sync", dependencies=[Depends(require_admin)])
def sync_data_plans(db: Session = Depends(get_db)):
    """
    Manually trigger a sync from all providers.
    """
    logger.info("Manual data plan sync triggered.")
    
    # 1. SMEPlug (Airtel)
    try:
        sme = SMEPlugProvider()
        items = sme.get_airtel_plans()
        for item in items:
            item["provider"] = "smeplug"
            _upsert_plan_from_provider(db, item)
        db.commit()
    except Exception as e:
        logger.error("SMEPlug sync failed: %s", e)

    # 2. Amigo (MTN/Glo)
    try:
        amigo = AmigoClient()
        res = amigo.fetch_data_plans()
        items = res.get("data", [])
        for item in items:
            _upsert_plan_from_provider(db, item)
        db.commit()
    except Exception as e:
        logger.error("Amigo sync failed: %s", e)

    # 3. 9mobile (Bills)
    try:
        provider = get_bills_provider()
        if hasattr(provider, "fetch_data_variations"):
            items = provider.fetch_data_variations("9mobile")
            for item in items:
                item["network"] = "9mobile"
                item["provider"] = str(getattr(provider, "name", "clubkonnect")).lower()
                _upsert_plan_from_provider(db, item)
            db.commit()
    except Exception as e:
        logger.error("9mobile sync failed: %s", e)

    return {"message": "Data plan sync completed."}
