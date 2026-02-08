import secrets
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User, Transaction, TransactionType, TransactionStatus
from app.schemas.wallet import WalletOut, FundWalletRequest, LedgerOut
from app.services.wallet import get_or_create_wallet, credit_wallet
from app.services.paystack import create_paystack_checkout, verify_paystack_signature
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
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")

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

    paystack_response = create_paystack_checkout(
        email=user.email,
        amount_kobo=int(payload.amount * 100),
        reference=reference,
        callback_url=payload.callback_url,
    )
    return paystack_response


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

    if event == "charge.success":
        reference = data.get("reference")
        transaction = db.query(Transaction).filter(Transaction.reference == reference).first()
        if transaction and transaction.status == TransactionStatus.PENDING:
            wallet = get_or_create_wallet(db, transaction.user_id)
            credit_wallet(db, wallet, Decimal(transaction.amount), reference, "Wallet funding via Paystack")
            transaction.status = TransactionStatus.SUCCESS
            transaction.external_reference = data.get("id")
            db.commit()

    return {"status": "ok"}
