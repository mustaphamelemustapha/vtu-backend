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
from app.services.amigo import (
    AmigoClient,
    AmigoApiError,
    canonical_plan_code,
    normalize_plan_code,
    resolve_network_id,
    split_plan_code,
)
from app.services.fraud import enforce_purchase_limits
from app.services.wallet import get_or_create_wallet, debit_wallet, credit_wallet
from app.services.pricing import get_price_for_user
from app.middlewares.rate_limit import limiter
from app.utils.cache import get_cached, set_cached

router = APIRouter()
settings = get_settings()


_SUCCESS_STATUS = {"success", "successful", "delivered", "completed", "ok", "done"}
_PENDING_STATUS = {"pending", "processing", "queued", "in_progress", "accepted", "submitted"}
_FAILURE_STATUS = {"failed", "fail", "error", "rejected", "declined", "cancelled", "canceled", "refunded"}

_SUCCESS_HINTS = ("successfully", "delivered", "gifted", "completed")
_FAILURE_HINTS = ("failed", "unsuccessful", "unable", "error", "rejected", "declined", "cancelled", "canceled")
_PENDING_HINTS = ("pending", "processing", "queued", "in progress", "submitted")


def _safe_reason(value: str, limit: int = 255) -> str:
    text = str(value or "").strip()
    return text[:limit] if text else "Unknown provider error"


def _normalize_provider_text(value) -> str:
    return str(value or "").strip().lower()


def _provider_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    raw = _normalize_provider_text(value)
    if raw in {"true", "1", "yes", "ok", "success", "successful", "delivered"}:
        return True
    if raw in {"false", "0", "no", "failed", "fail", "error", "unsuccessful"}:
        return False
    return None


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _classify_provider_outcome(response: dict) -> tuple[TransactionStatus, str]:
    status_text = _normalize_provider_text(
        response.get("status") or response.get("delivery_status") or response.get("state")
    )
    message_text = _normalize_provider_text(response.get("message") or response.get("detail") or "")
    error_text = _normalize_provider_text(response.get("error") or response.get("errors") or "")
    success_flag = _provider_bool(response.get("success"))

    success_signal = (
        success_flag is True
        or status_text in _SUCCESS_STATUS
        or _contains_any(message_text, _SUCCESS_HINTS)
    )
    failure_signal = (
        success_flag is False
        or status_text in _FAILURE_STATUS
        or bool(error_text)
        or _contains_any(message_text, _FAILURE_HINTS)
    )
    pending_signal = status_text in _PENDING_STATUS or _contains_any(message_text, _PENDING_HINTS)

    if success_signal and not failure_signal:
        return TransactionStatus.SUCCESS, ""
    if failure_signal and not success_signal:
        reason = (
            response.get("message")
            or response.get("detail")
            or response.get("error")
            or response.get("errors")
            or "Data provider rejected purchase"
        )
        return TransactionStatus.FAILED, _safe_reason(str(reason))
    if pending_signal or (success_signal and failure_signal):
        return TransactionStatus.PENDING, ""
    return TransactionStatus.PENDING, ""


def _upsert_plan_from_provider(db: Session, item: dict) -> bool:
    network = str(item.get("network") or "").strip().lower()
    if not network:
        return False

    incoming_code = str(item.get("plan_code") or "").strip()
    if not incoming_code:
        return False

    canonical_code = canonical_plan_code(network, incoming_code)
    if not canonical_code:
        return False

    _, provider_code = split_plan_code(canonical_code)
    plan = (
        db.query(DataPlan)
        .filter(DataPlan.plan_code == canonical_code)
        .first()
    )
    if not plan and provider_code:
        # Migrate legacy records that used plain numeric codes.
        plan = (
            db.query(DataPlan)
            .filter(DataPlan.network == network, DataPlan.plan_code == provider_code)
            .first()
        )
        if plan:
            plan.plan_code = canonical_code

    if not plan:
        plan = DataPlan(
            network=network,
            plan_code=canonical_code,
            plan_name=str(item.get("plan_name") or "").strip() or f"{network.upper()} {provider_code}",
            data_size=str(item.get("data_size") or "").strip() or "—",
            validity=str(item.get("validity") or "").strip() or "30d",
            base_price=Decimal(str(item.get("price", 0))),
            is_active=True,
        )
        db.add(plan)
        return True

    plan.network = network
    plan.plan_name = str(item.get("plan_name") or plan.plan_name).strip() or plan.plan_name
    plan.data_size = str(item.get("data_size") or plan.data_size).strip() or plan.data_size
    plan.validity = str(item.get("validity") or plan.validity).strip() or plan.validity
    plan.base_price = Decimal(str(item.get("price", plan.base_price)))
    plan.is_active = True
    return True


@router.get("/plans", response_model=list[DataPlanOut])

def list_data_plans(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cache_key = f"plans:{user.role.value}"
    cached = get_cached(cache_key)
    if cached:
        return cached

    plans = db.query(DataPlan).filter(DataPlan.is_active == True).all()
    should_sync = not plans
    if should_sync:
        # Auto-seed/refresh from provider when DB is empty or a newly supported
        # network (e.g. Airtel) is missing.
        client = AmigoClient()
        response = client.fetch_data_plans()
        items = response.get("data", [])
        touched = 0
        for item in items:
            touched += 1 if _upsert_plan_from_provider(db, item) else 0
        if touched:
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
    plan_code_input = str(payload.plan_code or "").strip()
    plan = db.query(DataPlan).filter(DataPlan.plan_code == plan_code_input, DataPlan.is_active == True).first()
    if not plan and ":" not in plan_code_input and plan_code_input:
        suffix_matches = (
            db.query(DataPlan)
            .filter(DataPlan.plan_code.like(f"%:{plan_code_input}"), DataPlan.is_active == True)
            .all()
        )
        if len(suffix_matches) == 1:
            plan = suffix_matches[0]
        elif len(suffix_matches) > 1:
            raise HTTPException(
                status_code=400,
                detail="Plan code is ambiguous across multiple networks. Refresh plans and retry.",
            )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    price = get_price_for_user(db, plan, user.role)
    wallet = get_or_create_wallet(db, user.id)
    enforce_purchase_limits(db, user_id=user.id, amount=Decimal(price), tx_type=TransactionType.DATA.value)
    if Decimal(wallet.balance) < Decimal(price):
        raise HTTPException(status_code=400, detail="Insufficient balance")

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

    debit_wallet(
        db,
        wallet,
        Decimal(price),
        reference,
        f"Data purchase to {str(payload.phone_number or '').strip()}",
    )

    client = AmigoClient()
    start = time.time()
    try:
        network_id = resolve_network_id(plan.network, plan.plan_code)
        if network_id is None:
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = f"Unsupported network: {plan.network}"
            credit_wallet(db, wallet, Decimal(price), reference, "Auto refund for unsupported network")
            transaction.status = TransactionStatus.REFUNDED
            db.commit()
            raise HTTPException(status_code=400, detail="Unsupported network for data purchase")

        response = client.purchase_data(
            {
                "network": network_id,
                "mobile_number": payload.phone_number,
                "plan": normalize_plan_code(plan.plan_code),
                "Ported_number": payload.ported_number,
            },
            idempotency_key=reference,
        )
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

        outcome_status, outcome_reason = _classify_provider_outcome(response)
        if outcome_status == TransactionStatus.SUCCESS:
            transaction.status = TransactionStatus.SUCCESS
            transaction.failure_reason = None
            transaction.external_reference = (
                response.get("reference")
                or response.get("transaction_reference")
                or response.get("transaction_id")
            )
        elif outcome_status == TransactionStatus.FAILED:
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = outcome_reason or _safe_reason(response.get("message"))
            credit_wallet(db, wallet, Decimal(price), reference, "Auto refund for failed data purchase")
            transaction.status = TransactionStatus.REFUNDED
        else:
            transaction.status = TransactionStatus.PENDING
            transaction.failure_reason = None

        db.commit()
        return {
            "reference": reference,
            "status": transaction.status,
            "message": str(response.get("message") or "").strip(),
            "test_mode": settings.amigo_test_mode,
        }
    except HTTPException:
        raise
    except AmigoApiError as exc:
        duration_ms = round((time.time() - start) * 1000, 2)
        db.add(ApiLog(
            user_id=user.id,
            service="amigo",
            endpoint="/data/purchase",
            status_code=exc.status_code or 502,
            duration_ms=duration_ms,
            reference=reference,
            success=0,
        ))
        transaction.status = TransactionStatus.FAILED
        transaction.failure_reason = _safe_reason(exc.message)
        credit_wallet(db, wallet, Decimal(price), reference, "Auto refund due to Amigo error")
        transaction.status = TransactionStatus.REFUNDED
        db.commit()
        raise HTTPException(
            status_code=502,
            detail=(
                f"Data provider failed: {_safe_reason(exc.message, 140)}. Wallet refunded. "
                "If this persists, set AMIGO_TEST_MODE=true temporarily."
            ),
        )
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
        transaction.failure_reason = _safe_reason(str(exc))
        credit_wallet(db, wallet, Decimal(price), reference, "Auto refund due to Amigo error")
        transaction.status = TransactionStatus.REFUNDED
        db.commit()
        raise HTTPException(status_code=502, detail="Data provider temporarily unavailable. Wallet refunded.")


@router.post("/sync")
def sync_data_plans(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    client = AmigoClient()
    response = client.fetch_data_plans()
    plans = response.get("data", [])
    updated = 0
    for item in plans:
        updated += 1 if _upsert_plan_from_provider(db, item) else 0
    db.commit()
    return {"updated": updated}
