import secrets
import re
import logging
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.core.config import get_settings
from app.core.security import verify_pin
from app.dependencies import get_current_user, require_admin
from app.models import User, Transaction, TransactionType, TransactionStatus
from sqlalchemy import or_
from app.schemas.wallet import WalletOut, FundWalletRequest, LedgerOut, BankTransferAccountsResponse, CreateBankTransferAccountsRequest, BankAccountOut, TransferVerifyRequest, TransferVerifyResponse, TransferRequest
from app.services.wallet import get_or_create_wallet, credit_wallet, verify_transfer_recipient, execute_wallet_transfer
from app.services.referrals import record_referral_first_deposit_reward
from app.services.paystack import (
    create_paystack_checkout,
    verify_paystack_signature,
    verify_paystack_transaction,
    get_or_create_dedicated_account,
    get_paystack_customer,
    PaystackError,
)
from app.services.monnify import (
    init_monnify_transaction,
    verify_monnify_signature,
    reserve_monnify_account,
    get_reserved_account_details,
    update_monnify_kyc_info,
)
from app.services.billstack import (
    generate_billstack_virtual_account,
    upgrade_billstack_kyc,
)
from app.middlewares.rate_limit import limiter
from app.models import WalletLedger
from app.models.virtual_account import VirtualAccount, VirtualAccountProvider, VirtualAccountStatus

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
                "bank_code": a.get("bankCode") or a.get("bank_code") or "000",
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


def _maybe_reward_first_deposit(db: Session, *, user: User, reference: str, amount: Decimal, status: TransactionStatus) -> None:
    try:
        record_referral_first_deposit_reward(
            db,
            user=user,
            transaction_reference=reference,
            deposit_amount=amount,
            transaction_status=status.value if hasattr(status, "value") else str(status),
        )
    except Exception as exc:
        db.rollback()
        logger.warning("Referral first deposit reward failed for %s: %s", reference, exc)


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
    all_accounts = []
    requires_kyc = False
    requires_phone = False
    messages = []
    account_reference = _transfer_account_reference(user)

    # 1. Fetch/Create Paystack Dedicated Account if Enabled
    if settings.paystack_dedicated_enabled:
        db_paystack = db.query(VirtualAccount).filter(
            VirtualAccount.user_id == user.id,
            VirtualAccount.provider == VirtualAccountProvider.PAYSTACK
        ).all()
        if db_paystack:
            for db_acc in db_paystack:
                all_accounts.append({
                    "bank_name": db_acc.bank_name,
                    "account_number": db_acc.account_number,
                    "account_name": db_acc.account_name,
                })
        else:
            phone = _to_paystack_phone(user.phone_number)
            try:
                first = (user.full_name or "").strip().split(" ")[0] or "Mele"
                last = " ".join((user.full_name or "").strip().split(" ")[1:]) or first
                dedicated = get_or_create_dedicated_account(
                    email=user.email,
                    first_name=first,
                    last_name=last,
                    phone=phone,
                )
                paystack_accs = _parse_paystack_dedicated_account(dedicated)
                if paystack_accs:
                    for acc in paystack_accs:
                        bank_slug = acc["bank_name"].lower().replace(" ", "_")
                        db_acc = VirtualAccount(
                            user_id=user.id,
                            provider=VirtualAccountProvider.PAYSTACK,
                            account_number=acc["account_number"],
                            account_name=acc["account_name"],
                            bank_name=acc["bank_name"],
                            bank_code="000",
                            customer_reference=f"{account_reference}_paystack_{bank_slug}",
                            reservation_reference=f"paystack_{user.id}_{bank_slug}_{secrets.token_hex(4)}",
                            status=VirtualAccountStatus.ACTIVE,
                        )
                        db.add(db_acc)
                    db.commit()
                    all_accounts.extend(paystack_accs)
            except PaystackError as exc:
                detail = str(exc)
                lower_detail = detail.lower()
                if (not phone) and ("phone" in lower_detail or "mobile" in lower_detail):
                    requires_phone = True
                else:
                    requires_kyc = True
                messages.append(f"Paystack: {detail}")
            except Exception as exc:
                db.rollback()
                logger.warning("Auto-generate Paystack account failed: %s", exc)
                requires_kyc = True
                messages.append(f"Paystack: {exc}")

    # 2. Fetch/Create Monnify Reserved Account
    db_monnify = db.query(VirtualAccount).filter(
        VirtualAccount.user_id == user.id,
        VirtualAccount.provider == VirtualAccountProvider.MONNIFY
    ).all()

    if not db_monnify:
        # Since Monnify now strictly requires BVN or NIN, auto-generating here 
        # without KYC will always fail and exhaust database connections.
        # We skip the API call and just flag requires_kyc = True. The frontend 
        # will prompt the user and hit the POST endpoint with the raw BVN/NIN.
        requires_kyc = True
        messages.append("Please provide your BVN or NIN to generate your Monnify account.")
    else:
        for db_acc in db_monnify:
            all_accounts.append({
                "bank_name": db_acc.bank_name,
                "account_number": db_acc.account_number,
                "account_name": db_acc.account_name,
            })

    # 3. Fetch/Create Billstack Reserved Account
    if settings.billstack_enabled:
        db_billstack = db.query(VirtualAccount).filter(
            VirtualAccount.user_id == user.id,
            VirtualAccount.provider == VirtualAccountProvider.BILLSTACK
        ).all()

        if not db_billstack:
            try:
                first = (user.full_name or "").strip().split(" ")[0] or "Mele"
                last = " ".join((user.full_name or "").strip().split(" ")[1:]) or first
                resp = generate_billstack_virtual_account(
                    email=user.email,
                    reference=f"{account_reference}_billstack_{settings.billstack_preferred_bank.lower()}",
                    phone=user.phone_number or "",
                    first_name=first,
                    last_name=last,
                    bank=settings.billstack_preferred_bank,
                )
                if resp.get("status") is True:
                    data = resp.get("data", {})
                    accounts = data.get("account") or []
                    reservation_ref = data.get("reference") or secrets.token_hex(8)
                    for acc in accounts:
                        db_acc = VirtualAccount(
                            user_id=user.id,
                            provider=VirtualAccountProvider.BILLSTACK,
                            account_number=acc["account_number"],
                            account_name=acc["account_name"],
                            bank_name=acc["bank_name"],
                            bank_code=acc.get("bank_id", "000"),
                            customer_reference=f"{account_reference}_billstack_{settings.billstack_preferred_bank.lower()}",
                            reservation_reference=reservation_ref,
                            status=VirtualAccountStatus.ACTIVE,
                        )
                        db.add(db_acc)
                    db.commit()
                    for acc in accounts:
                        all_accounts.append({
                            "bank_name": acc["bank_name"],
                            "account_number": acc["account_number"],
                            "account_name": acc["account_name"],
                        })
                else:
                    messages.append("Billstack account generation is pending.")
            except Exception as exc:
                db.rollback()
                logger.warning("Auto-generate Billstack account failed: %s", exc)
                messages.append(f"Billstack: {exc}")
        else:
            for db_acc in db_billstack:
                all_accounts.append({
                    "bank_name": db_acc.bank_name,
                    "account_number": db_acc.account_number,
                    "account_name": db_acc.account_name,
                })

    # Sort accounts: Palmpay first, then Moniepoint/Monnify, then others
    def sort_key(acc):
        bank_name = str(acc.get("bank_name", "")).lower()
        if "palmpay" in bank_name:
            return 0
        if "moniepoint" in bank_name or "monnify" in bank_name:
            return 1
        return 2
    all_accounts.sort(key=sort_key)

    return {
        "provider": "combined",
        "account_reference": account_reference,
        "accounts": all_accounts,
        "requires_kyc": requires_kyc,
        "requires_phone": requires_phone if not all_accounts else False,
        "message": " | ".join(messages) if messages else None,
    }


@router.post("/bank-transfer-accounts", response_model=BankTransferAccountsResponse)
@limiter.limit("5/minute")
def create_bank_transfer_accounts(request: Request, payload: CreateBankTransferAccountsRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    all_accounts = []
    requires_kyc = False
    requires_phone = False
    messages = []
    account_reference = _transfer_account_reference(user)

    # 1. Create/Update Paystack Dedicated Account if Enabled
    if settings.paystack_dedicated_enabled:
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

        first = (user.full_name or "").strip().split(" ")[0] or "Mele"
        last = " ".join((user.full_name or "").strip().split(" ")[1:]) or first
        try:
            # Flush existing Paystack virtual accounts for this user in DB
            db.query(VirtualAccount).filter(
                VirtualAccount.user_id == user.id,
                VirtualAccount.provider == VirtualAccountProvider.PAYSTACK
            ).delete()
            db.commit()

            dedicated = get_or_create_dedicated_account(
                email=user.email,
                first_name=first,
                last_name=last,
                phone=paystack_phone,
            )
            paystack_accs = _parse_paystack_dedicated_account(dedicated)
            if paystack_accs:
                for acc in paystack_accs:
                    bank_slug = acc["bank_name"].lower().replace(" ", "_")
                    db_acc = VirtualAccount(
                        user_id=user.id,
                        provider=VirtualAccountProvider.PAYSTACK,
                        account_number=acc["account_number"],
                        account_name=acc["account_name"],
                        bank_name=acc["bank_name"],
                        bank_code="000",
                        customer_reference=f"{account_reference}_paystack_{bank_slug}",
                        reservation_reference=f"paystack_{user.id}_{bank_slug}_{secrets.token_hex(4)}",
                        status=VirtualAccountStatus.ACTIVE,
                    )
                    db.add(db_acc)
                db.commit()
                all_accounts.extend(paystack_accs)
        except PaystackError as exc:
            detail = str(exc)
            lower_detail = detail.lower()
            if (not phone) and ("phone" in lower_detail or "mobile" in lower_detail):
                requires_phone = True
            else:
                requires_kyc = True
            messages.append(f"Paystack: {detail}")
        except Exception as exc:
            db.rollback()
            logger.warning("Recreate Paystack account failed: %s", exc)
            requires_kyc = True
            messages.append(f"Paystack: {exc}")

    # 2. Clear Database Monnify Cache & Re-generate (Hard Refresh)
    import hashlib
    import re
    bvn = re.sub(r"\D", "", (payload.bvn or "").strip())
    nin = re.sub(r"\D", "", (payload.nin or "").strip())

    bvn_hash_val = None
    nin_hash_val = None

    if bvn:
        bvn_hash_val = hashlib.sha256(bvn.encode()).hexdigest()
        dup_bvn = db.query(User).filter(User.bvn_hash == bvn_hash_val, User.id != user.id).first()
        if dup_bvn:
            raise HTTPException(
                status_code=400,
                detail="This BVN is already linked to another account on our platform. Please use a unique BVN."
            )

    if nin:
        nin_hash_val = hashlib.sha256(nin.encode()).hexdigest()
        dup_nin = db.query(User).filter(User.nin_hash == nin_hash_val, User.id != user.id).first()
        if dup_nin:
            raise HTTPException(
                status_code=400,
                detail="This NIN is already linked to another account on our platform. Please use a unique NIN."
            )

    # Flush any existing Monnify virtual accounts for this user first
    db.query(VirtualAccount).filter(
        VirtualAccount.user_id == user.id,
        VirtualAccount.provider == VirtualAccountProvider.MONNIFY
    ).delete()
    db.commit()

    try:
        try:
            resp = reserve_monnify_account(
                account_reference=account_reference,
                account_name=f"MMTECHGLOBE/{user.full_name}",
                customer_email=user.email,
                customer_name=user.full_name,
                bvn=bvn or None,
                nin=nin or None,
                get_all_available_banks=True,
            )
        except ValueError as exc:
            if "already exists" in str(exc).lower() or "duplicate" in str(exc).lower() or "exists" in str(exc).lower():
                # Account already exists, try to update KYC and fetch it
                if bvn or nin:
                    update_monnify_kyc_info(
                        account_reference=account_reference,
                        bvn=bvn or None,
                        nin=nin or None,
                    )
                resp = get_reserved_account_details(account_reference=account_reference)
            else:
                raise
        accounts_data = _parse_reserved_accounts(resp)
        if accounts_data:
            # Uniqueness check: Prevent duplicate KYC mapping across users
            for acc in accounts_data:
                dup = db.query(VirtualAccount).filter(
                    VirtualAccount.account_number == acc["account_number"],
                    VirtualAccount.user_id != user.id
                ).first()
                if dup:
                    raise HTTPException(
                        status_code=400,
                        detail="This KYC document (BVN/NIN) is already linked to another account on our platform. Please use a unique BVN/NIN."
                    )

            body = resp.get("responseBody") or resp.get("data") or resp.get("response") or {}
            reservation_ref = body.get("reservationReference") or secrets.token_hex(8)
            for acc in accounts_data:
                bank_slug = acc["bank_name"].lower().replace(" ", "_")
                db_acc = VirtualAccount(
                    user_id=user.id,
                    provider=VirtualAccountProvider.MONNIFY,
                    account_number=acc["account_number"],
                    account_name=acc["account_name"],
                    bank_name=acc["bank_name"],
                    bank_code=acc.get("bank_code", "000"),
                    customer_reference=f"{account_reference}_monnify_{bank_slug}",
                    reservation_reference=f"{reservation_ref}_{bank_slug}",
                    status=VirtualAccountStatus.ACTIVE,
                )
                db.add(db_acc)
            if bvn_hash_val or nin_hash_val:
                db_user = db.query(User).filter(User.id == user.id).first()
                if db_user:
                    if bvn_hash_val:
                        db_user.bvn_hash = bvn_hash_val
                    if nin_hash_val:
                        db_user.nin_hash = nin_hash_val
            db.commit()
            all_accounts.extend(accounts_data)
        else:
            requires_kyc = True
            messages.append("Failed to reserve Monnify account.")
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        requires_kyc = True
        messages.append(f"Monnify: {exc}")

    # 3. Recreate / Update Billstack Reserved Account
    if settings.billstack_enabled:
        db.query(VirtualAccount).filter(
            VirtualAccount.user_id == user.id,
            VirtualAccount.provider == VirtualAccountProvider.BILLSTACK
        ).delete()
        db.commit()

        try:
            first = (user.full_name or "").strip().split(" ")[0] or "Mele"
            last = " ".join((user.full_name or "").strip().split(" ")[1:]) or first
            resp = generate_billstack_virtual_account(
                email=user.email,
                reference=f"{account_reference}_billstack_{settings.billstack_preferred_bank.lower()}",
                phone=user.phone_number or "",
                first_name=first,
                last_name=last,
                bank=settings.billstack_preferred_bank,
            )
            if resp.get("status") is True:
                data = resp.get("data", {})
                accounts = data.get("account") or []
                reservation_ref = data.get("reference") or secrets.token_hex(8)
                for acc in accounts:
                    db_acc = VirtualAccount(
                        user_id=user.id,
                        provider=VirtualAccountProvider.BILLSTACK,
                        account_number=acc["account_number"],
                        account_name=acc["account_name"],
                        bank_name=acc["bank_name"],
                        bank_code=acc.get("bank_id", "000"),
                        customer_reference=f"{account_reference}_billstack_{settings.billstack_preferred_bank.lower()}",
                        reservation_reference=reservation_ref,
                        status=VirtualAccountStatus.ACTIVE,
                        )
                    db.add(db_acc)
                db.commit()
                for acc in accounts:
                    all_accounts.append({
                        "bank_name": acc["bank_name"],
                        "account_number": acc["account_number"],
                        "account_name": acc["account_name"],
                    })
                
                # Upgrade KYC if BVN is provided
                if bvn:
                    try:
                        upgrade_billstack_kyc(email=user.email, bvn=bvn)
                    except Exception as kyc_exc:
                        logger.warning("Billstack KYC upgrade failed: %s", kyc_exc)
                        messages.append(f"Billstack KYC: {kyc_exc}")
            else:
                messages.append("Failed to reserve Billstack account.")
        except Exception as exc:
            db.rollback()
            logger.warning("Recreate Billstack account failed: %s", exc)
            messages.append(f"Billstack: {exc}")

    if not all_accounts and messages:
        raise HTTPException(status_code=502, detail=" | ".join(messages))

    # Sort accounts: Palmpay first, then Moniepoint/Monnify, then others
    def sort_key(acc):
        bank_name = str(acc.get("bank_name", "")).lower()
        if "palmpay" in bank_name:
            return 0
        if "moniepoint" in bank_name or "monnify" in bank_name:
            return 1
        return 2
    all_accounts.sort(key=sort_key)

    return {
        "provider": "combined",
        "account_reference": account_reference,
        "accounts": all_accounts,
        "requires_kyc": requires_kyc,
        "requires_phone": requires_phone if not all_accounts else False,
        "message": " | ".join(messages) if messages else None,
    }


@router.delete("/bank-transfer-accounts")
def delete_bank_transfer_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(VirtualAccount).filter(
        VirtualAccount.user_id == user.id,
        VirtualAccount.provider.in_([VirtualAccountProvider.MONNIFY, VirtualAccountProvider.BILLSTACK])
    ).delete()
    db.commit()
    return {"status": "success", "message": "Virtual accounts cache successfully deleted."}


@router.get("/temp-reset")
def temp_reset_virtual_accounts(email: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user:
        return {"status": "error", "message": "User not found"}
    deleted = db.query(VirtualAccount).filter(
        VirtualAccount.user_id == user.id,
        VirtualAccount.provider.in_([VirtualAccountProvider.MONNIFY, VirtualAccountProvider.BILLSTACK])
    ).delete()
    db.commit()
    return {"status": "success", "message": f"Successfully deleted {deleted} cached accounts for {email}."}



@router.post("/monnify/reserved-account", response_model=BankTransferAccountsResponse)
@limiter.limit("5/minute")
def create_monnify_reserved_account(request: Request, payload: CreateBankTransferAccountsRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_bank_transfer_accounts(request, payload, user, db)


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







@router.get("/paystack/verify")
def paystack_verify(reference: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transaction = db.query(Transaction).filter(Transaction.reference == reference, Transaction.user_id == user.id).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if transaction.status == TransactionStatus.SUCCESS:
        _maybe_reward_first_deposit(
            db,
            user=user,
            reference=reference,
            amount=Decimal(transaction.amount),
            status=TransactionStatus.SUCCESS,
        )
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
            _maybe_reward_first_deposit(
                db,
                user=user,
                reference=reference,
                amount=Decimal(transaction.amount),
                status=TransactionStatus.SUCCESS,
            )
            return {"status": "success"}
    except Exception:
        pass
    return {"status": "pending"}

# --- Backward Compatibility for Webhooks ---
# Webhooks were moved to webhooks.py, but providers may still have the old /wallet/... URLs configured.
from app.api.v1.endpoints.webhooks import paystack_webhook, monnify_webhook, billstack_webhook, smeplug_webhook

router.post("/paystack/webhook", include_in_schema=False)(paystack_webhook)
router.post("/monnify/webhook", include_in_schema=False)(monnify_webhook)
router.post("/billstack/webhook", include_in_schema=False)(billstack_webhook)
router.post("/smeplug/webhook", include_in_schema=False)(smeplug_webhook)
router.post("/smeplug", include_in_schema=False)(smeplug_webhook)
router.post("/monnify", include_in_schema=False)(monnify_webhook)
router.post("/billstack", include_in_schema=False)(billstack_webhook)

@router.post("/transfer/verify", response_model=TransferVerifyResponse)
@limiter.limit("10/minute")
def verify_transfer(request: Request, payload: TransferVerifyRequest, db: Session = Depends(get_db)):
    recipient = verify_transfer_recipient(db, payload.identifier)
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found. Check the username, email, or phone number.")
    
    # Mask identifier for privacy
    def mask(text: str) -> str:
        if "@" in text:
            name, ext = text.split("@", 1)
            return f"{name[:2]}***@{ext}"
        if len(text) > 4:
            return f"{text[:2]}***{text[-2:]}"
        return text

    return TransferVerifyResponse(
        full_name=recipient.full_name
    )

@router.post("/transfer")
@limiter.limit("5/minute")
def execute_transfer(request: Request, payload: TransferRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero.")
        
    recipient = verify_transfer_recipient(db, payload.identifier)
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found.")
        
    if recipient.id == user.id:
        raise HTTPException(status_code=400, detail="You cannot transfer to yourself.")
        
    success = execute_wallet_transfer(db, sender=user, recipient=recipient, amount=payload.amount)
    if not success:
        raise HTTPException(status_code=400, detail="Insufficient wallet balance.")
        
    return {"status": "success", "message": f"Successfully transferred NGN{payload.amount:,.2f} to {recipient.full_name}"}
