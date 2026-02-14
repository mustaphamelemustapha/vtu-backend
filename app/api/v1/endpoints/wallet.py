import secrets
import re
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User, Transaction, TransactionType, TransactionStatus
from app.schemas.wallet import WalletOut, FundWalletRequest, LedgerOut, BankTransferAccountsResponse, CreateBankTransferAccountsRequest, BankAccountOut
from app.services.wallet import get_or_create_wallet, credit_wallet
from app.services.paystack import create_paystack_checkout, verify_paystack_signature, verify_paystack_transaction
from app.services.monnify import init_monnify_transaction, verify_monnify_signature, reserve_monnify_account, get_reserved_account_details
from app.middlewares.rate_limit import limiter
from app.models import WalletLedger

router = APIRouter()

def _transfer_account_reference(user: User) -> str:
    # Stable per-user reference so we can fetch accounts later without DB storage.
    return f"AXISVTU_{user.id}"


def _parse_reserved_accounts(payload: dict) -> list[dict]:
    body = payload.get("responseBody") or payload.get("data") or payload.get("response") or {}
    accounts = body.get("accounts") or []
    out = []
    for a in accounts:
        out.append(
            {
                "bank_name": a.get("bankName") or a.get("bank") or a.get("bank_name") or "Bank",
                "account_number": a.get("accountNumber") or a.get("account_number") or "",
                "account_name": a.get("accountName") or a.get("account_name"),
            }
        )
    return [a for a in out if a.get("account_number")]


def _safe_ref(prefix: str, value: str) -> str:
    raw = f"{prefix}_{value or ''}"
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    return cleaned[:64] if len(cleaned) <= 64 else cleaned[:64]


@router.get("/me", response_model=WalletOut)
def get_wallet(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = get_or_create_wallet(db, user.id)
    return wallet


@router.get("/ledger", response_model=list[LedgerOut])
def get_ledger(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = get_or_create_wallet(db, user.id)
    entries = db.query(WalletLedger).filter(WalletLedger.wallet_id == wallet.id).order_by(WalletLedger.id.desc()).limit(50).all()
    return entries


@router.get("/bank-transfer-accounts", response_model=BankTransferAccountsResponse)
def get_bank_transfer_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account_reference = _transfer_account_reference(user)
    details = get_reserved_account_details(account_reference=account_reference)
    if details.get("__not_found__"):
        return {
            "provider": "monnify",
            "account_reference": account_reference,
            "accounts": [],
            "requires_kyc": True,
        }
    accounts = _parse_reserved_accounts(details)
    return {
        "provider": "monnify",
        "account_reference": account_reference,
        "accounts": accounts,
        "requires_kyc": False if accounts else True,
    }


@router.post("/bank-transfer-accounts", response_model=BankTransferAccountsResponse)
@limiter.limit("5/minute")
def create_bank_transfer_accounts(request: Request, payload: CreateBankTransferAccountsRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account_reference = _transfer_account_reference(user)
    bvn = (payload.bvn or "").strip()
    nin = (payload.nin or "").strip()
    if not bvn and not nin:
        raise HTTPException(status_code=400, detail="BVN or NIN is required to generate bank transfer accounts")

    resp = reserve_monnify_account(
        account_reference=account_reference,
        account_name=f"AxisVTU Wallet - {user.full_name}",
        customer_email=user.email,
        customer_name=user.full_name,
        bvn=bvn or None,
        nin=nin or None,
        get_all_available_banks=True,
    )
    accounts = _parse_reserved_accounts(resp)
    return {
        "provider": "monnify",
        "account_reference": account_reference,
        "accounts": accounts,
        "requires_kyc": False if accounts else True,
    }


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
    payment_status = (data.get("paymentStatus") or data.get("status") or "").upper()

    if payment_status in {"PAID", "SUCCESSFUL", "SUCCESS"} or (event_type and "SUCCESS" in event_type):
        # If we initiated the payment, we will have a transaction with our reference.
        if transaction:
            if transaction.status != TransactionStatus.SUCCESS:
                wallet = get_or_create_wallet(db, transaction.user_id)
                credit_wallet(db, wallet, Decimal(transaction.amount), reference, "Wallet funding via Monnify")
                transaction.status = TransactionStatus.SUCCESS
                transaction.external_reference = data.get("transactionReference") or data.get("paymentReference")
                db.commit()
        else:
            # Reserved account / bank transfer funding: Monnify may not use our internal reference.
            customer = data.get("customer") or {}
            email = (customer.get("email") or "").strip().lower()
            amount_paid = data.get("amountPaid") or data.get("amount") or data.get("amountPaidInKobo")
            ext_ref = data.get("transactionReference") or data.get("paymentReference") or reference
            if email and amount_paid:
                user = db.query(User).filter(User.email == email).first()
                if user:
                    # Idempotency: don't double-credit the same Monnify transactionReference.
                    if ext_ref and db.query(Transaction).filter(Transaction.external_reference == ext_ref).first():
                        return {"status": "ok"}
                    amt = Decimal(str(amount_paid))
                    tx_ref = _safe_ref("TRF", str(ext_ref or secrets.token_hex(8)))
                    tx = Transaction(
                        user_id=user.id,
                        reference=tx_ref,
                        amount=amt,
                        status=TransactionStatus.SUCCESS,
                        tx_type=TransactionType.WALLET_FUND,
                        external_reference=str(ext_ref) if ext_ref else None,
                    )
                    db.add(tx)
                    wallet = get_or_create_wallet(db, user.id)
                    credit_wallet(db, wallet, amt, tx_ref, "Wallet funding via bank transfer (Monnify)")

    if payment_status in {"FAILED", "CANCELLED"} or (event_type and "FAILED" in event_type):
        if transaction and transaction.status == TransactionStatus.PENDING:
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
