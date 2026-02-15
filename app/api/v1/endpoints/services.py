import secrets
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.middlewares.rate_limit import limiter
from app.models import User, TransactionStatus, TransactionType, ServiceTransaction
from app.schemas.services import (
    AirtimePurchaseRequest,
    CablePurchaseRequest,
    ElectricityPurchaseRequest,
    ExamPurchaseRequest,
    ServicesCatalogOut,
)
from app.services.bills import MockBillsProvider
from app.services.wallet import get_or_create_wallet, debit_wallet, credit_wallet

router = APIRouter()


def _ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


@router.get("/catalog", response_model=ServicesCatalogOut)
def services_catalog(user: User = Depends(get_current_user)):
    # Keep it simple: a UI-ready catalog. Real providers can replace this later.
    return {
        "airtime_networks": ["mtn", "glo", "airtel", "9mobile"],
        "cable_providers": [
            {"id": "dstv", "name": "DStv"},
            {"id": "gotv", "name": "GOtv"},
            {"id": "startimes", "name": "StarTimes"},
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
    wallet = get_or_create_wallet(db, user.id)
    amount = Decimal(payload.amount)
    if Decimal(wallet.balance) < amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = _ref("AIRTIME")
    tx = ServiceTransaction(
        user_id=user.id,
        reference=reference,
        tx_type=TransactionType.AIRTIME.value,
        amount=amount,
        status=TransactionStatus.PENDING.value,
        provider=payload.network.strip().lower(),
        customer=payload.phone_number.strip(),
        meta={"network": payload.network.strip().lower(), "phone_number": payload.phone_number.strip()},
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    debit_wallet(db, wallet, amount, reference, "Airtime purchase")

    provider = MockBillsProvider()
    result = provider.purchase_airtime(tx.provider or "", tx.customer or "", float(amount))
    if result.success:
        tx.status = TransactionStatus.SUCCESS.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        db.commit()
        return {"reference": reference, "status": tx.status}

    tx.failure_reason = result.message or "Provider failed"
    credit_wallet(db, wallet, amount, reference, "Auto refund for failed airtime purchase")
    tx.status = TransactionStatus.REFUNDED.value
    db.commit()
    raise HTTPException(status_code=502, detail=tx.failure_reason)


@router.post("/cable/purchase")
@limiter.limit("5/minute")
def purchase_cable(request: Request, payload: CablePurchaseRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = get_or_create_wallet(db, user.id)
    amount = Decimal(payload.amount)
    if Decimal(wallet.balance) < amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = _ref("CABLE")
    tx = ServiceTransaction(
        user_id=user.id,
        reference=reference,
        tx_type=TransactionType.CABLE.value,
        amount=amount,
        status=TransactionStatus.PENDING.value,
        provider=payload.provider.strip().lower(),
        customer=payload.smartcard_number.strip(),
        product_code=payload.package_code.strip(),
        meta={
            "provider": payload.provider.strip().lower(),
            "smartcard_number": payload.smartcard_number.strip(),
            "package_code": payload.package_code.strip(),
        },
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    debit_wallet(db, wallet, amount, reference, "Cable subscription")

    provider = MockBillsProvider()
    result = provider.purchase_cable(tx.provider or "", tx.customer or "", tx.product_code or "", float(amount))
    if result.success:
        tx.status = TransactionStatus.SUCCESS.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        db.commit()
        return {"reference": reference, "status": tx.status}

    tx.failure_reason = result.message or "Provider failed"
    credit_wallet(db, wallet, amount, reference, "Auto refund for failed cable purchase")
    tx.status = TransactionStatus.REFUNDED.value
    db.commit()
    raise HTTPException(status_code=502, detail=tx.failure_reason)


@router.post("/electricity/purchase")
@limiter.limit("5/minute")
def purchase_electricity(request: Request, payload: ElectricityPurchaseRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = get_or_create_wallet(db, user.id)
    amount = Decimal(payload.amount)
    if Decimal(wallet.balance) < amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = _ref("ELECTRICITY")
    tx = ServiceTransaction(
        user_id=user.id,
        reference=reference,
        tx_type=TransactionType.ELECTRICITY.value,
        amount=amount,
        status=TransactionStatus.PENDING.value,
        provider=payload.disco.strip().lower(),
        customer=payload.meter_number.strip(),
        product_code=payload.meter_type.strip().lower(),
        meta={
            "disco": payload.disco.strip().lower(),
            "meter_number": payload.meter_number.strip(),
            "meter_type": payload.meter_type.strip().lower(),
        },
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    debit_wallet(db, wallet, amount, reference, "Electricity purchase")

    provider = MockBillsProvider()
    result = provider.purchase_electricity(tx.provider or "", tx.customer or "", tx.product_code or "", float(amount))
    if result.success:
        tx.status = TransactionStatus.SUCCESS.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        db.commit()
        return {"reference": reference, "status": tx.status, "token": (tx.meta or {}).get("token")}

    tx.failure_reason = result.message or "Provider failed"
    credit_wallet(db, wallet, amount, reference, "Auto refund for failed electricity purchase")
    tx.status = TransactionStatus.REFUNDED.value
    db.commit()
    raise HTTPException(status_code=502, detail=tx.failure_reason)


@router.post("/exam/purchase")
@limiter.limit("5/minute")
def purchase_exam_pin(request: Request, payload: ExamPurchaseRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # For pins we price "amount" as a fixed demo price per pin for now.
    unit_price = Decimal("2000.00")
    total_amount = unit_price * Decimal(int(payload.quantity or 1))

    wallet = get_or_create_wallet(db, user.id)
    if Decimal(wallet.balance) < total_amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    reference = _ref("EXAM")
    tx = ServiceTransaction(
        user_id=user.id,
        reference=reference,
        tx_type=TransactionType.EXAM.value,
        amount=total_amount,
        status=TransactionStatus.PENDING.value,
        provider=payload.exam.strip().lower(),
        customer=(payload.phone_number or "").strip() or None,
        product_code=str(int(payload.quantity or 1)),
        meta={
            "exam": payload.exam.strip().lower(),
            "quantity": int(payload.quantity or 1),
            "phone_number": (payload.phone_number or "").strip() or None,
            "unit_price": str(unit_price),
        },
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    debit_wallet(db, wallet, total_amount, reference, "Exam pin purchase")

    provider = MockBillsProvider()
    result = provider.purchase_exam_pin(tx.provider or "", int(payload.quantity or 1), tx.customer)
    if result.success:
        tx.status = TransactionStatus.SUCCESS.value
        tx.external_reference = result.external_reference
        if result.meta:
            tx.meta = {**(tx.meta or {}), **result.meta}
        db.commit()
        return {"reference": reference, "status": tx.status, "pins": (tx.meta or {}).get("pins", [])}

    tx.failure_reason = result.message or "Provider failed"
    credit_wallet(db, wallet, total_amount, reference, "Auto refund for failed exam pin purchase")
    tx.status = TransactionStatus.REFUNDED.value
    db.commit()
    raise HTTPException(status_code=502, detail=tx.failure_reason)

