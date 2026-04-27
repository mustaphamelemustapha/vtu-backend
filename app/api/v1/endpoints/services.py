import hashlib
import secrets
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import inspect

from app.core.database import get_db
from app.dependencies import get_current_user
from app.middlewares.rate_limit import limiter
from app.models import User, TransactionStatus, TransactionType, ServiceTransaction
from app.schemas.services import (
    AirtimePurchaseRequest,
    CablePurchaseRequest,
    CableVerifyRequest,
    ElectricityPurchaseRequest,
    ExamPurchaseRequest,
    ServicesCatalogOut,
)
from app.services.bills import get_bills_provider
from app.services.fraud import enforce_purchase_limits
from app.services.wallet import get_or_create_wallet, debit_wallet, credit_wallet
from app.services.pricing import get_service_charge_for_user

router = APIRouter()
logger = logging.getLogger(__name__)
_PROVIDER_PENDING_STATUS = {"pending", "processing", "queued", "in_progress", "submitted", "accepted"}
_TRANSPORT_ERROR_MARKERS = (
    "network error",
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "connection refused",
    "temporarily unavailable",
    "service unavailable",
)
_PENDING_CONFIRMATION_MESSAGE = "Provider confirmation delayed. Purchase is being verified. Check history shortly."

_NETWORK_PREFIXES: dict[str, set[str]] = {
    "mtn": {
        "0803", "0806", "0703", "0706", "0810", "0813", "0814", "0816",
        "0903", "0906", "0913", "0916", "0704", "07025", "07026",
    },
    "airtel": {
        "0802", "0808", "0708", "0812", "0701", "0902", "0907", "0901", "0912",
    },
    "glo": {
        "0805", "0807", "0705", "0811", "0815", "0905", "0915",
    },
    "9mobile": {
        "0809", "0817", "0818", "0908", "0909",
    },
}


def _ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _client_request_reference(prefix: str, user_id: int, request_id: str | None) -> str | None:
    raw = str(request_id or "").strip()
    if not raw:
        return None
    digest = hashlib.sha256(f"{prefix}:{user_id}:{raw}".encode()).hexdigest()[:24].upper()
    return f"{prefix}_{digest}"


def _provider_status(result) -> str:
    meta = result.meta or {}
    for provider_key in ("vtpass", "clubkonnect"):
        status = str((meta.get(provider_key) or {}).get("status") or "").strip().lower()
        if status:
            return status
    return str(meta.get("status") or "").strip().lower()


def _is_transport_error(exc: Exception) -> bool:
    message = str(exc or "").strip().lower()
    return any(marker in message for marker in _TRANSPORT_ERROR_MARKERS)


def _mark_pending_confirmation(db: Session, tx: ServiceTransaction, result_meta: dict | None = None) -> None:
    tx.status = TransactionStatus.PENDING.value
    tx.failure_reason = _PENDING_CONFIRMATION_MESSAGE
    if result_meta:
        tx.meta = {**(tx.meta or {}), **result_meta}
    db.commit()


def _normalize_phone_for_network_inference(phone_number: str) -> str:
    digits = "".join(ch for ch in str(phone_number or "") if ch.isdigit())
    if digits.startswith("234") and len(digits) >= 13:
        return f"0{digits[3:]}"
    return digits


def _infer_nigeria_network(phone_number: str) -> str | None:
    normalized = _normalize_phone_for_network_inference(phone_number)
    if len(normalized) < 4:
        return None
    prefix5 = normalized[:5]
    prefix4 = normalized[:4]
    for network, prefixes in _NETWORK_PREFIXES.items():
        if prefix5 in prefixes or prefix4 in prefixes:
            return network
    return None


def _ensure_service_table(db: Session):
    try:
        if not inspect(db.bind).has_table("service_transactions"):
            raise HTTPException(
                status_code=503,
                detail="Services database is not ready yet. Enable AUTO_CREATE_TABLES=true once and redeploy to create required tables.",
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Services database is not ready yet.")


@router.get("/catalog", response_model=ServicesCatalogOut)
def services_catalog(user: User = Depends(get_current_user)):
    # Keep it simple: a UI-ready catalog. Real providers can replace this later.
    return {
        "airtime_networks": ["mtn", "glo", "airtel", "9mobile"],
        "cable_providers": [
            {"id": "dstv", "name": "DStv"},
            {"id": "gotv", "name": "GOtv"},
            {"id": "startimes", "name": "StarTimes"},
            {"id": "showmax", "name": "Showmax"},
        ],
        "electricity_discos": [
            "ikeja",
            "eko",
            "abuja",
            "kano",
            "ibadan",
            "enugu",
            "portharcourt",
            "kaduna",
        ],
        "exam_types": ["waec", "neco", "jamb"],
    }


@router.post("/airtime/purchase")
@limiter.limit("5/minute")
def purchase_airtime(request: Request, payload: AirtimePurchaseRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_service_table(db)
    wallet = get_or_create_wallet(db, user.id)
    base_amount = Decimal(payload.amount)
    charge_amount, margin = get_service_charge_for_user(
        db,
        tx_type=TransactionType.AIRTIME.value,
        provider=payload.network,
        base_amount=base_amount,
        user_role=user.role,
    )
    if charge_amount <= 0:
        raise HTTPException(status_code=400, detail="Final amount must be greater than zero")
    selected_network = str(payload.network or "").strip().lower()
    inferred_network = _infer_nigeria_network(payload.phone_number)
    if inferred_network and inferred_network != selected_network:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Phone number appears to be {inferred_network.upper()}. "
                f"Selected network {selected_network.upper()} does not match."
            ),
        )
    enforce_purchase_limits(db, user_id=user.id, amount=charge_amount, tx_type=TransactionType.AIRTIME.value)
    if Decimal(wallet.balance) < charge_amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = _client_request_reference("AIRTIME", user.id, getattr(payload, "client_request_id", None)) or _ref("AIRTIME")
    existing = db.query(ServiceTransaction).filter(ServiceTransaction.user_id == user.id, ServiceTransaction.reference == reference).first()
    if existing:
        return {"reference": existing.reference, "status": existing.status, "message": existing.failure_reason or ""}
    tx = ServiceTransaction(
        user_id=user.id,
        reference=reference,
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
    db.refresh(tx)

    debit_wallet(db, wallet, charge_amount, reference, "Airtime purchase")

    provider = get_bills_provider()
    try:
        result = provider.purchase_airtime(tx.provider or "", tx.customer or "", float(base_amount))
    except Exception as exc:
        if _is_transport_error(exc):
            logger.warning("Airtime provider confirmation delayed ref=%s error=%s", reference, exc)
            _mark_pending_confirmation(db, tx, {"provider_error": str(exc)})
            return {"reference": reference, "status": tx.status, "message": _PENDING_CONFIRMATION_MESSAGE}
        logger.exception("Airtime purchase failed with non-transport provider error ref=%s", reference)
        tx.failure_reason = str(exc) or "Provider failed"
        credit_wallet(db, wallet, charge_amount, reference, "Auto refund for failed airtime purchase")
        tx.status = TransactionStatus.REFUNDED.value
        db.commit()
        raise HTTPException(status_code=502, detail=tx.failure_reason)
    provider_status = _provider_status(result)
    if provider_status in _PROVIDER_PENDING_STATUS:
        tx.status = TransactionStatus.PENDING.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        tx.failure_reason = result.message or _PENDING_CONFIRMATION_MESSAGE
        db.commit()
        return {"reference": reference, "status": tx.status, "message": tx.failure_reason}
    if result.success:
        tx.status = TransactionStatus.SUCCESS.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        db.commit()
        return {"reference": reference, "status": tx.status}

    tx.failure_reason = result.message or "Provider failed"
    credit_wallet(db, wallet, charge_amount, reference, "Auto refund for failed airtime purchase")
    tx.status = TransactionStatus.REFUNDED.value
    db.commit()
    raise HTTPException(status_code=502, detail=tx.failure_reason)


@router.post("/cable/purchase")
@limiter.limit("5/minute")
def purchase_cable(request: Request, payload: CablePurchaseRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_service_table(db)
    wallet = get_or_create_wallet(db, user.id)
    base_amount = Decimal(payload.amount)
    charge_amount, margin = get_service_charge_for_user(
        db,
        tx_type=TransactionType.CABLE.value,
        provider=payload.provider,
        base_amount=base_amount,
        user_role=user.role,
    )
    if charge_amount <= 0:
        raise HTTPException(status_code=400, detail="Final amount must be greater than zero")
    enforce_purchase_limits(db, user_id=user.id, amount=charge_amount, tx_type=TransactionType.CABLE.value)
    if Decimal(wallet.balance) < charge_amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = _client_request_reference("CABLE", user.id, getattr(payload, "client_request_id", None)) or _ref("CABLE")
    existing = db.query(ServiceTransaction).filter(ServiceTransaction.user_id == user.id, ServiceTransaction.reference == reference).first()
    if existing:
        return {"reference": existing.reference, "status": existing.status, "message": existing.failure_reason or ""}
    tx = ServiceTransaction(
        user_id=user.id,
        reference=reference,
        tx_type=TransactionType.CABLE.value,
        amount=charge_amount,
        status=TransactionStatus.PENDING.value,
        provider=payload.provider.strip().lower(),
        customer=payload.smartcard_number.strip(),
        product_code=payload.package_code.strip(),
        meta={
            "provider": payload.provider.strip().lower(),
            "smartcard_number": payload.smartcard_number.strip(),
            "phone_number": payload.phone_number.strip(),
            "package_code": payload.package_code.strip(),
            "base_amount": str(base_amount),
            "margin_applied": str(margin),
            "charge_amount": str(charge_amount),
        },
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    debit_wallet(db, wallet, charge_amount, reference, "Cable subscription")

    provider = get_bills_provider()
    try:
        result = provider.purchase_cable(
            tx.provider or "",
            tx.customer or "",
            tx.product_code or "",
            float(base_amount),
            payload.phone_number.strip(),
        )
    except Exception as exc:
        if _is_transport_error(exc):
            logger.warning("Cable provider confirmation delayed ref=%s error=%s", reference, exc)
            _mark_pending_confirmation(db, tx, {"provider_error": str(exc)})
            return {"reference": reference, "status": tx.status, "message": _PENDING_CONFIRMATION_MESSAGE}
        logger.exception("Cable purchase failed with non-transport provider error ref=%s", reference)
        tx.failure_reason = str(exc) or "Provider failed"
        credit_wallet(db, wallet, charge_amount, reference, "Auto refund for failed cable purchase")
        tx.status = TransactionStatus.REFUNDED.value
        db.commit()
        raise HTTPException(status_code=502, detail=tx.failure_reason)
    provider_status = _provider_status(result)
    if provider_status in _PROVIDER_PENDING_STATUS:
        tx.status = TransactionStatus.PENDING.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        tx.failure_reason = result.message or _PENDING_CONFIRMATION_MESSAGE
        db.commit()
        return {"reference": reference, "status": tx.status, "message": tx.failure_reason}
    if result.success:
        tx.status = TransactionStatus.SUCCESS.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        db.commit()
        return {"reference": reference, "status": tx.status}

    tx.failure_reason = result.message or "Provider failed"
    credit_wallet(db, wallet, charge_amount, reference, "Auto refund for failed cable purchase")
    tx.status = TransactionStatus.REFUNDED.value
    db.commit()
    raise HTTPException(status_code=502, detail=tx.failure_reason)


@router.get("/cable/packages")
def cable_packages(provider: str, user: User = Depends(get_current_user)):
    provider_key = str(provider or "").strip().lower()
    if not provider_key:
        raise HTTPException(status_code=400, detail="Provider is required.")
    service = get_bills_provider()
    fetcher = getattr(service, "fetch_cable_packages", None)
    if not callable(fetcher):
        return {"provider": provider_key, "packages": []}
    try:
        rows = fetcher(provider_key) or []
    except Exception as exc:
        logger.warning("Cable packages fetch failed provider=%s error=%s", provider_key, exc)
        return {"provider": provider_key, "packages": []}
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        name = str(row.get("name") or code).strip()
        amount_raw = row.get("amount")
        try:
            amount = float(amount_raw) if amount_raw not in (None, "") else None
        except Exception:
            amount = None
        normalized.append(
            {
                "code": code,
                "name": name or code,
                "amount": amount,
                "provider": str(row.get("provider") or provider_key).strip().lower(),
            }
        )
    return {"provider": provider_key, "packages": normalized}


@router.post("/cable/verify")
@limiter.limit("15/minute")
def cable_verify(
    request: Request,
    payload: CableVerifyRequest,
    user: User = Depends(get_current_user),
):
    provider_key = str(payload.provider or "").strip().lower()
    smartcard = str(payload.smartcard_number or "").strip()
    if not provider_key or not smartcard:
        raise HTTPException(status_code=400, detail="Provider and smartcard number are required.")
    service = get_bills_provider()
    verifier = getattr(service, "verify_cable_customer", None)
    if not callable(verifier):
        return {"ok": False, "message": "Smartcard verification is unavailable right now."}
    try:
        result = verifier(provider_key, smartcard) or {}
    except Exception as exc:
        logger.warning("Cable verify failed provider=%s smartcard=%s error=%s", provider_key, smartcard[-4:], exc)
        return {"ok": False, "message": "Unable to verify smartcard right now."}
    ok = bool(result.get("ok"))
    return {
        "ok": ok,
        "customer_name": str(result.get("customer_name") or "").strip() if ok else "",
        "message": "" if ok else str(result.get("message") or "Unable to verify smartcard number.").strip(),
    }


@router.post("/electricity/purchase")
@limiter.limit("5/minute")
def purchase_electricity(request: Request, payload: ElectricityPurchaseRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_service_table(db)
    wallet = get_or_create_wallet(db, user.id)
    base_amount = Decimal(payload.amount)
    charge_amount, margin = get_service_charge_for_user(
        db,
        tx_type=TransactionType.ELECTRICITY.value,
        provider=payload.disco,
        base_amount=base_amount,
        user_role=user.role,
    )
    if charge_amount <= 0:
        raise HTTPException(status_code=400, detail="Final amount must be greater than zero")
    enforce_purchase_limits(db, user_id=user.id, amount=charge_amount, tx_type=TransactionType.ELECTRICITY.value)
    if Decimal(wallet.balance) < charge_amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = _client_request_reference("ELECTRICITY", user.id, getattr(payload, "client_request_id", None)) or _ref("ELECTRICITY")
    existing = db.query(ServiceTransaction).filter(ServiceTransaction.user_id == user.id, ServiceTransaction.reference == reference).first()
    if existing:
        return {"reference": existing.reference, "status": existing.status, "message": existing.failure_reason or "", "token": (existing.meta or {}).get("token")}
    tx = ServiceTransaction(
        user_id=user.id,
        reference=reference,
        tx_type=TransactionType.ELECTRICITY.value,
        amount=charge_amount,
        status=TransactionStatus.PENDING.value,
        provider=payload.disco.strip().lower(),
        customer=payload.meter_number.strip(),
        product_code=payload.meter_type.strip().lower(),
        meta={
            "disco": payload.disco.strip().lower(),
            "meter_number": payload.meter_number.strip(),
            "meter_type": payload.meter_type.strip().lower(),
            "phone_number": payload.phone_number.strip(),
            "base_amount": str(base_amount),
            "margin_applied": str(margin),
            "charge_amount": str(charge_amount),
        },
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    debit_wallet(db, wallet, charge_amount, reference, "Electricity purchase")

    provider = get_bills_provider()
    try:
        result = provider.purchase_electricity(
            tx.provider or "",
            tx.customer or "",
            tx.product_code or "",
            float(base_amount),
            payload.phone_number.strip(),
        )
    except Exception as exc:
        if _is_transport_error(exc):
            logger.warning("Electricity provider confirmation delayed ref=%s error=%s", reference, exc)
            _mark_pending_confirmation(db, tx, {"provider_error": str(exc)})
            return {"reference": reference, "status": tx.status, "message": _PENDING_CONFIRMATION_MESSAGE, "token": (tx.meta or {}).get("token")}
        logger.exception("Electricity purchase failed with non-transport provider error ref=%s", reference)
        tx.failure_reason = str(exc) or "Provider failed"
        credit_wallet(db, wallet, charge_amount, reference, "Auto refund for failed electricity purchase")
        tx.status = TransactionStatus.REFUNDED.value
        db.commit()
        raise HTTPException(status_code=502, detail=tx.failure_reason)
    provider_status = _provider_status(result)
    if provider_status in _PROVIDER_PENDING_STATUS:
        tx.status = TransactionStatus.PENDING.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        tx.failure_reason = result.message or _PENDING_CONFIRMATION_MESSAGE
        db.commit()
        return {"reference": reference, "status": tx.status, "token": (tx.meta or {}).get("token"), "message": tx.failure_reason}
    if result.success:
        tx.status = TransactionStatus.SUCCESS.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        db.commit()
        return {"reference": reference, "status": tx.status, "token": (tx.meta or {}).get("token")}

    tx.failure_reason = result.message or "Provider failed"
    credit_wallet(db, wallet, charge_amount, reference, "Auto refund for failed electricity purchase")
    tx.status = TransactionStatus.REFUNDED.value
    db.commit()
    raise HTTPException(status_code=502, detail=tx.failure_reason)


@router.post("/exam/purchase")
@limiter.limit("5/minute")
def purchase_exam_pin(request: Request, payload: ExamPurchaseRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_service_table(db)
    # For pins we price "amount" as a fixed demo price per pin for now.
    unit_price = Decimal("2000.00")
    base_total_amount = unit_price * Decimal(int(payload.quantity or 1))
    charge_amount, margin = get_service_charge_for_user(
        db,
        tx_type=TransactionType.EXAM.value,
        provider=payload.exam,
        base_amount=base_total_amount,
        user_role=user.role,
    )
    if charge_amount <= 0:
        raise HTTPException(status_code=400, detail="Final amount must be greater than zero")
    enforce_purchase_limits(db, user_id=user.id, amount=charge_amount, tx_type=TransactionType.EXAM.value)

    wallet = get_or_create_wallet(db, user.id)
    if Decimal(wallet.balance) < charge_amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = _client_request_reference("EXAM", user.id, getattr(payload, "client_request_id", None)) or _ref("EXAM")
    existing = db.query(ServiceTransaction).filter(ServiceTransaction.user_id == user.id, ServiceTransaction.reference == reference).first()
    if existing:
        return {"reference": existing.reference, "status": existing.status, "message": existing.failure_reason or "", "pins": (existing.meta or {}).get("pins", [])}
    tx = ServiceTransaction(
        user_id=user.id,
        reference=reference,
        tx_type=TransactionType.EXAM.value,
        amount=charge_amount,
        status=TransactionStatus.PENDING.value,
        provider=payload.exam.strip().lower(),
        customer=(payload.phone_number or "").strip() or None,
        product_code=str(int(payload.quantity or 1)),
        meta={
            "exam": payload.exam.strip().lower(),
            "quantity": int(payload.quantity or 1),
            "phone_number": (payload.phone_number or "").strip() or None,
            "unit_price": str(unit_price),
            "base_amount": str(base_total_amount),
            "margin_applied": str(margin),
            "charge_amount": str(charge_amount),
        },
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    debit_wallet(db, wallet, charge_amount, reference, "Exam pin purchase")

    provider = get_bills_provider()
    try:
        result = provider.purchase_exam_pin(tx.provider or "", int(payload.quantity or 1), tx.customer)
    except Exception as exc:
        if _is_transport_error(exc):
            logger.warning("Exam provider confirmation delayed ref=%s error=%s", reference, exc)
            _mark_pending_confirmation(db, tx, {"provider_error": str(exc)})
            return {"reference": reference, "status": tx.status, "pins": (tx.meta or {}).get("pins", []), "message": _PENDING_CONFIRMATION_MESSAGE}
        logger.exception("Exam purchase failed with non-transport provider error ref=%s", reference)
        tx.failure_reason = str(exc) or "Provider failed"
        credit_wallet(db, wallet, charge_amount, reference, "Auto refund for failed exam pin purchase")
        tx.status = TransactionStatus.REFUNDED.value
        db.commit()
        raise HTTPException(status_code=502, detail=tx.failure_reason)
    provider_status = _provider_status(result)
    if provider_status in _PROVIDER_PENDING_STATUS:
        tx.status = TransactionStatus.PENDING.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        tx.failure_reason = result.message or _PENDING_CONFIRMATION_MESSAGE
        db.commit()
        return {"reference": reference, "status": tx.status, "pins": (tx.meta or {}).get("pins", []), "message": tx.failure_reason}
    if result.success:
        tx.status = TransactionStatus.SUCCESS.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        db.commit()
        return {"reference": reference, "status": tx.status, "pins": (tx.meta or {}).get("pins", [])}

    tx.failure_reason = result.message or "Provider failed"
    credit_wallet(db, wallet, charge_amount, reference, "Auto refund for failed exam pin purchase")
    tx.status = TransactionStatus.REFUNDED.value
    db.commit()
    raise HTTPException(status_code=502, detail=tx.failure_reason)
