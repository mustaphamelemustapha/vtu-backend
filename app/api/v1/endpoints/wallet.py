import secrets
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User, Transaction, TransactionType, TransactionStatus
from app.schemas.wallet import WalletOut, FundWalletRequest, LedgerOut
from app.services.wallet import get_or_create_wallet, credit_wallet
from app.services.paystack import create_paystack_checkout, verify_paystack_signature, verify_paystack_transaction
from app.services.monnify import init_monnify_transaction, verify_monnify_signature
from app.middlewares.rate_limit import limiter
from app.models import WalletLedger

router = APIRouter()


@router.get("/me", response_model=WalletOut)
def get_wallet(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = get_or_create_wallet(db, user.id)
    return wallet


@router.get("/ledger", response_model=list[LedgerOut])
def get_ledger(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = get_or_create_wallet(db, user.id)
    entries = db.query(WalletLedger).filter(WalletLedger.wallet_id == wallet.id).order_by(WalletLedger.id.desc()).limit(50).all()
    return entries


@router.post("/fund")
@limiter.limit("5/minute")
def fund_wallet(request: Request, payload: FundWalletRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.amount < 100:
        raise HTTPException(status_code=400, detail="Minimum amount is 100")

    reference = f"FUND_{secrets.token_hex(8)}"
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        amount=payload.amount,
        status=TransactionStatus.PENDING,
        tx_type=TransactionType.WALLET_FUND,
    )
    db.add(transaction)
    db.commit()

    callback_url = payload.callback_url or request.headers.get("origin") or str(request.base_url)
    try:
        paystack_response = create_paystack_checkout(
            email=user.email,
            amount_kobo=int(payload.amount * 100),
            reference=reference,
            callback_url=callback_url,
        )
        return paystack_response
    except Exception:
        transaction.status = TransactionStatus.FAILED
        db.commit()
        raise HTTPException(status_code=502, detail="Payment initialization failed")


@router.post("/monnify/init")
@limiter.limit("5/minute")
def monnify_init(request: Request, payload: FundWalletRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.amount < 100:
        raise HTTPException(status_code=400, detail="Minimum amount is 100")

    reference = f"FUND_{secrets.token_hex(8)}"
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        amount=payload.amount,
        status=TransactionStatus.PENDING,
        tx_type=TransactionType.WALLET_FUND,
    )
    db.add(transaction)
    db.commit()

    callback_url = payload.callback_url or request.headers.get("origin") or str(request.base_url)
    try:
        resp = init_monnify_transaction(
            email=user.email,
            name=user.full_name,
            amount=float(payload.amount),
            reference=reference,
            callback_url=callback_url,
        )
        return resp
    except Exception as exc:
        transaction.status = TransactionStatus.FAILED
        db.commit()
        raise HTTPException(status_code=502, detail=f"Payment initialization failed: {exc}")


@router.post("/monnify/webhook")
async def monnify_webhook(request: Request, db: Session = Depends(get_db)):
    signature = request.headers.get("monnify-signature")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    body = await request.body()
    if not verify_monnify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event_type = payload.get("eventType")
    data = payload.get("eventData", {}) or payload.get("data", {})

    reference = data.get("paymentReference") or data.get("transactionReference")
    transaction = db.query(Transaction).filter(Transaction.reference == reference).first()
    if not transaction:
        return {"status": "ok"}

    payment_status = (data.get("paymentStatus") or data.get("status") or "").upper()

    if payment_status in {"PAID", "SUCCESSFUL", "SUCCESS"} or (event_type and "SUCCESS" in event_type):
        if transaction.status != TransactionStatus.SUCCESS:
            wallet = get_or_create_wallet(db, transaction.user_id)
            credit_wallet(db, wallet, Decimal(transaction.amount), reference, "Wallet funding via Monnify")
            transaction.status = TransactionStatus.SUCCESS
            transaction.external_reference = data.get("transactionReference") or data.get("paymentReference")
            db.commit()

    if payment_status in {"FAILED", "CANCELLED"} or (event_type and "FAILED" in event_type):
        if transaction.status == TransactionStatus.PENDING:
            transaction.status = TransactionStatus.FAILED
            transaction.external_reference = data.get("transactionReference") or data.get("paymentReference")
            db.commit()

    return {"status": "ok"}


@router.post("/paystack/webhook")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    signature = request.headers.get("x-paystack-signature")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    body = await request.body()
    if not verify_paystack_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data", {})

    reference = data.get("reference")
    transaction = db.query(Transaction).filter(Transaction.reference == reference).first()

    if event == "charge.success" and transaction:
        if transaction.status != TransactionStatus.SUCCESS:
            wallet = get_or_create_wallet(db, transaction.user_id)
            credit_wallet(db, wallet, Decimal(transaction.amount), reference, "Wallet funding via Paystack")
            transaction.status = TransactionStatus.SUCCESS
            transaction.external_reference = data.get("id")
            db.commit()

    if event == "charge.failed" and transaction:
        if transaction.status == TransactionStatus.PENDING:
            transaction.status = TransactionStatus.FAILED
            transaction.external_reference = data.get("id")
            db.commit()

    return {"status": "ok"}


@router.get("/paystack/verify")
def paystack_verify(reference: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transaction = db.query(Transaction).filter(Transaction.reference == reference, Transaction.user_id == user.id).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if transaction.status == TransactionStatus.SUCCESS:
        return {"status": "success"}

    try:
        response = verify_paystack_transaction(reference)
        data = response.get("data", {})
        if data.get("status") == "success":
            wallet = get_or_create_wallet(db, transaction.user_id)
            credit_wallet(db, wallet, Decimal(transaction.amount), reference, "Wallet funding via Paystack")
            transaction.status = TransactionStatus.SUCCESS
            transaction.external_reference = data.get("id")
            db.commit()
            return {"status": "success"}
    except Exception:
        pass
    return {"status": "pending"}
