import secrets
import re
import logging
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.core.config import get_settings
from app.dependencies import get_current_user
from app.models import User, Transaction, TransactionType, TransactionStatus
from app.schemas.wallet import WalletOut, FundWalletRequest, LedgerOut, BankTransferAccountsResponse, CreateBankTransferAccountsRequest, BankAccountOut
from app.services.wallet import get_or_create_wallet, credit_wallet
from app.services.paystack import (
    create_paystack_checkout,
    verify_paystack_signature,
    verify_paystack_transaction,
    get_or_create_dedicated_account,
    get_paystack_customer,
    PaystackError,
)
from app.services.monnify import init_monnify_transaction, verify_monnify_signature, reserve_monnify_account, get_reserved_account_details
from app.middlewares.rate_limit import limiter
from app.models import WalletLedger

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

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


def _parse_paystack_dedicated_account(payload: dict) -> list[dict]:
    account = payload or {}
    bank = account.get("bank") or {}
    return [
        {
            "bank_name": bank.get("name") or bank.get("bank_name") or "Bank",
            "account_number": account.get("account_number") or "",
            "account_name": account.get("account_name"),
        }
    ]


def _safe_ref(prefix: str, value: str) -> str:
    raw = f"{prefix}_{value or ''}"
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    return cleaned[:64] if len(cleaned) <= 64 else cleaned[:64]


def _normalize_phone(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or None


def _canonical_phone(value: str | None) -> str | None:
    digits = _normalize_phone(value)
    if not digits:
        return None
    if digits.startswith("234") and len(digits) >= 13:
        return f"0{digits[3:13]}"
    if digits.startswith("0") and len(digits) == 11:
        return digits
    if len(digits) == 10:
        return f"0{digits}"
    return digits


def _phone_variants(value: str | None) -> set[str]:
    digits = _normalize_phone(value)
    if not digits:
        return set()
    out = {digits}
    if digits.startswith("234") and len(digits) >= 13:
        out.add(f"0{digits[3:13]}")
        out.add(digits[3:13])
    if digits.startswith("0") and len(digits) == 11:
        out.add(f"234{digits[1:]}")
        out.add(digits[1:])
    if len(digits) == 10:
        out.add(f"0{digits}")
        out.add(f"234{digits}")
    return {item for item in out if item}


def _find_user_by_phone(db: Session, phone: str | None, exclude_user_id: int | None = None) -> User | None:
    variants = _phone_variants(phone)
    if not variants:
        return None
    users = db.query(User).filter(User.phone_number.isnot(None)).all()
    for candidate in users:
        if exclude_user_id is not None and candidate.id == exclude_user_id:
            continue
        if _phone_variants(candidate.phone_number) & variants:
            return candidate
    return None


def _extract_paystack_email(data: dict) -> str:
    customer = data.get("customer") or {}
    metadata = data.get("metadata") or {}
    email = (
        customer.get("email")
        or metadata.get("customer_email")
        or metadata.get("email")
        or ""
    )
    email = str(email).strip().lower()
    if email:
        return email

    customer_code = (
        customer.get("customer_code")
        or customer.get("code")
        or customer.get("id")
        or ""
    )
    if customer_code:
        try:
            customer_data = (get_paystack_customer(str(customer_code)).get("data") or {})
            email = str(customer_data.get("email") or "").strip().lower()
            if email:
                return email
        except Exception as exc:
            logger.warning("Paystack customer lookup failed for %s: %s", customer_code, exc)
    return ""


def _resolve_paystack_transfer_user(db: Session, data: dict) -> User | None:
    email = _extract_paystack_email(data)
    if email:
        user = db.query(User).filter(User.email == email).first()
        if user:
            return user

    customer = data.get("customer") or {}
    metadata = data.get("metadata") or {}
    authorization = data.get("authorization") or {}
    phone = (
        customer.get("phone")
        or metadata.get("phone_number")
        or metadata.get("phone")
        or authorization.get("sender_phone")
        or ""
    )
    user = _find_user_by_phone(db, str(phone))
    if user:
        return user
    return None


def _to_paystack_phone(value: str | None) -> str | None:
    digits = _normalize_phone(value)
    if not digits:
        return None
    if digits.startswith("234") and len(digits) >= 13:
        return digits[:13]
    if digits.startswith("0") and len(digits) == 11:
        return f"234{digits[1:]}"
    if len(digits) == 10:
        return f"234{digits}"
    return digits


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
    if settings.bank_transfer_provider.lower() == "paystack" and settings.paystack_dedicated_enabled:
        phone = _to_paystack_phone(user.phone_number)
        try:
            first = (user.full_name or "").strip().split(" ")[0] or "Axis"
            last = " ".join((user.full_name or "").strip().split(" ")[1:]) or first
            dedicated = get_or_create_dedicated_account(
                email=user.email,
                first_name=first,
                last_name=last,
                phone=phone,
            )
            accounts = _parse_paystack_dedicated_account(dedicated)
            return {
                "provider": "paystack",
                "account_reference": _transfer_account_reference(user),
                "accounts": accounts,
                "requires_kyc": False if accounts else True,
                "requires_phone": False,
                "message": None if accounts else "Account generation is pending. Please try again shortly.",
            }
        except PaystackError as exc:
            detail = str(exc)
            lower_detail = detail.lower()
            requires_phone = (not phone) and ("phone" in lower_detail or "mobile" in lower_detail)
            return {
                "provider": "paystack",
                "account_reference": _transfer_account_reference(user),
                "accounts": [],
                "requires_kyc": True,
                "requires_phone": requires_phone,
                "message": (
                    "Paystack needs your phone number to generate your dedicated account."
                    if requires_phone
                    else f"Unable to fetch dedicated account right now: {detail}"
                ),
            }
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
    if settings.bank_transfer_provider.lower() == "paystack" and settings.paystack_dedicated_enabled:
        phone_input = _normalize_phone(payload.phone_number)
        if payload.phone_number is not None and (not phone_input or len(phone_input) < 10):
            raise HTTPException(status_code=400, detail="Enter a valid phone number")

        phone = phone_input or _normalize_phone(user.phone_number)
        paystack_phone = _to_paystack_phone(phone)
        if phone and not paystack_phone:
            raise HTTPException(status_code=400, detail="Enter a valid Nigerian phone number")

        canonical_phone = _canonical_phone(phone)
        if canonical_phone and canonical_phone != (user.phone_number or "").strip():
            existing = _find_user_by_phone(db, canonical_phone, exclude_user_id=user.id)
            if existing:
                raise HTTPException(status_code=400, detail="Phone number already registered")
            user.phone_number = canonical_phone
            db.commit()
            db.refresh(user)

        first = (user.full_name or "").strip().split(" ")[0] or "Axis"
        last = " ".join((user.full_name or "").strip().split(" ")[1:]) or first
        try:
            dedicated = get_or_create_dedicated_account(
                email=user.email,
                first_name=first,
                last_name=last,
                phone=paystack_phone,
            )
        except PaystackError as exc:
            detail = str(exc)
            lower_detail = detail.lower()
            if (not phone) and ("phone" in lower_detail or "mobile" in lower_detail):
                raise HTTPException(status_code=400, detail="Phone number is required to generate Paystack bank transfer account")
            raise HTTPException(status_code=502, detail=f"Paystack dedicated account failed: {detail}")
        accounts = _parse_paystack_dedicated_account(dedicated)
        return {
            "provider": "paystack",
            "account_reference": _transfer_account_reference(user),
            "accounts": accounts,
            "requires_kyc": False if accounts else True,
            "requires_phone": False,
            "message": None if accounts else "Account generation is pending. Please try again shortly.",
        }
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
        "requires_phone": False,
        "message": None if accounts else "Complete BVN or NIN to generate your dedicated account.",
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
                    try:
                        db.add(tx)
                        wallet = get_or_create_wallet(db, user.id)
                        credit_wallet(db, wallet, amt, tx_ref, "Wallet funding via bank transfer (Monnify)")
                    except IntegrityError:
                        db.rollback()
                        return {"status": "ok"}

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
    elif event == "charge.success" and not transaction:
        # Dedicated virtual account funding (no internal reference).
        ext_ref = str(data.get("id") or "").strip()
        amount_kobo = data.get("amount")
        user = _resolve_paystack_transfer_user(db, data)
        if user and amount_kobo:
            if ext_ref and db.query(Transaction).filter(Transaction.external_reference == ext_ref).first():
                return {"status": "ok"}
            amt = Decimal(str(amount_kobo)) / Decimal("100")
            tx_ref = _safe_ref("TRF", ext_ref or secrets.token_hex(8))
            tx = Transaction(
                user_id=user.id,
                reference=tx_ref,
                amount=amt,
                status=TransactionStatus.SUCCESS,
                tx_type=TransactionType.WALLET_FUND,
                external_reference=ext_ref or None,
            )
            try:
                db.add(tx)
                wallet = get_or_create_wallet(db, user.id)
                credit_wallet(db, wallet, amt, tx_ref, "Wallet funding via Paystack transfer")
                db.commit()
            except IntegrityError:
                db.rollback()
                return {"status": "ok"}
        else:
            logger.warning(
                "Paystack charge.success missing routing fields for transfer credit: user=%s amount=%s",
                bool(user),
                amount_kobo,
            )

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
