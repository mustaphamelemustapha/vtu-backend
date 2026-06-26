import logging
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from decimal import Decimal

import secrets
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.models import Transaction, TransactionStatus, TransactionType, User
from app.services.wallet import get_or_create_wallet, credit_wallet
from app.services.monnify import verify_monnify_signature
from app.services.billstack import verify_billstack_signature
from app.core.config import get_settings
from app.api.v1.endpoints.wallet import _maybe_reward_first_deposit, _safe_ref
from app.models.virtual_account import VirtualAccount, VirtualAccountProvider

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()

@router.post("/smeplug")
async def smeplug_webhook(request: Request, db: Session = Depends(get_db)):
    """
    SMEPlug Webhook Handler
    
    Payload format:
    {
        "transaction": {
            "status": "success" | "failed",
            "reference": "<provider_reference>",
            "customer_reference": "<internal_transaction_id>",
            "beneficiary": "<phone>",
            "price": "<amount>"
        }
    }
    """
    # Optional webhook token validation (supports either bearer auth or x-webhook-token).
    configured_secret = str(settings.smeplug_webhook_secret or "").strip()
    if configured_secret:
        auth_header = str(request.headers.get("authorization") or "")
        token_header = str(request.headers.get("x-webhook-token") or "")
        bearer = auth_header.replace("Bearer ", "").strip() if auth_header.lower().startswith("bearer ") else auth_header.strip()
        if configured_secret not in {bearer, token_header.strip()}:
            raise HTTPException(status_code=401, detail="Invalid SMEPlug webhook token")

    payload = await request.json()
    logger.info("SMEPlug Webhook received: %s", payload)
    
    tx_data = payload.get("transaction") or payload.get("data") or payload
    if not tx_data:
        logger.warning("SMEPlug Webhook missing transaction data")
        return {"status": "ignored"}

    status = str(tx_data.get("status") or "").strip().lower()
    customer_reference = tx_data.get("customer_reference")
    provider_reference = tx_data.get("reference")
    
    if not customer_reference:
        logger.warning("SMEPlug Webhook missing customer_reference")
        return {"status": "ignored"}

    # Find transaction using customer_reference
    transaction = db.query(Transaction).filter(Transaction.reference == customer_reference).first()
    if not transaction:
        logger.warning("SMEPlug Webhook: Transaction not found for ref %s", customer_reference)
        return {"status": "ignored"}

    # Idempotency check: if already success or refunded, ignore
    if transaction.status in {TransactionStatus.SUCCESS, TransactionStatus.REFUNDED}:
        logger.info("SMEPlug Webhook: Transaction %s already in terminal state %s", customer_reference, transaction.status)
        return {"status": "ok"}

    if status == "success":
        transaction.status = TransactionStatus.SUCCESS
        transaction.external_reference = provider_reference
        db.commit()
        try:
            from app.services.referrals import trigger_referral_data_activity
            trigger_referral_data_activity(db, transaction)
            db.commit()
        except Exception as ref_exc:
            logger.error("Failed to trigger referral activity in webhook: %s", ref_exc)
        logger.info("SMEPlug Webhook: Transaction %s marked as SUCCESS", customer_reference)
    elif status == "failed":
        transaction.status = TransactionStatus.FAILED
        transaction.external_reference = provider_reference
        
        # Refund wallet
        wallet = get_or_create_wallet(db, transaction.user_id)
        credit_wallet(
            db, 
            wallet, 
            Decimal(transaction.amount), 
            transaction.reference, 
            f"Refund for failed SMEPlug data purchase (Ref: {provider_reference})"
        )
        transaction.status = TransactionStatus.REFUNDED
        db.commit()
        logger.info("SMEPlug Webhook: Transaction %s marked as FAILED and REFUNDED", customer_reference)
    else:
        logger.info("SMEPlug Webhook: Transaction %s status is %s, keeping pending", customer_reference, status)

    return {"status": "ok"}

@router.post("/monnify")
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
        if transaction:
            if transaction.status != TransactionStatus.SUCCESS:
                customer = data.get("customer")
                customer_name = customer.get("name") if isinstance(customer, dict) else None
                sender_name = data.get("payerName") or customer_name
                if sender_name:
                    sender_name = str(sender_name).strip()
                else:
                    sender_name = None
                wallet = get_or_create_wallet(db, transaction.user_id)
                desc = f"Wallet funding from {sender_name}" if sender_name else "Wallet funding via Monnify"
                credit_wallet(db, wallet, Decimal(transaction.amount), reference, desc, sender_name=sender_name)
                transaction.status = TransactionStatus.SUCCESS
                transaction.external_reference = data.get("transactionReference") or data.get("paymentReference")
                db.commit()
            _maybe_reward_first_deposit(
                db,
                user=db.query(User).filter(User.id == transaction.user_id).first() or transaction.user,
                reference=reference,
                amount=Decimal(transaction.amount),
                status=TransactionStatus.SUCCESS,
            )
        else:
            amount_paid = data.get("amountPaid") or data.get("amount") or data.get("amountPaidInKobo")
            ext_ref = data.get("transactionReference") or data.get("paymentReference") or reference
            
            if amount_paid:
                customer = data.get("customer")
                customer_name = customer.get("name") if isinstance(customer, dict) else None
                sender_name = data.get("payerName") or customer_name
                if sender_name:
                    sender_name = str(sender_name).strip()
                else:
                    sender_name = None
                user = None
                
                # 1. Resolve user by accountReference pattern ("AXISVTU_{user_id}")
                account_ref = data.get("accountReference") or (data.get("product") or {}).get("reference")
                if account_ref:
                    account_ref_str = str(account_ref).strip()
                    if account_ref_str.startswith("AXISVTU_"):
                        try:
                            user_id_str = account_ref_str.split("_")[1]
                            user_id = int(user_id_str)
                            user = db.query(User).filter(User.id == user_id).first()
                            if user:
                                logger.info("Monnify Webhook: Resolved user ID %d directly from accountReference %s", user.id, account_ref_str)
                        except (ValueError, IndexError):
                            pass
                    
                    if not user:
                        # Query VirtualAccount table by customer_reference / reservation_reference
                        va = db.query(VirtualAccount).filter(
                            (VirtualAccount.reservation_reference == account_ref_str) |
                            (VirtualAccount.customer_reference == account_ref_str)
                        ).first()
                        if va:
                            user = va.user
                            if user:
                                logger.info("Monnify Webhook: Resolved user ID %d from VirtualAccount by accountReference %s", user.id, account_ref_str)

                # 2. Resolve user by destination account number (from destinationAccountPaymentResults or destinationAccountNumber)
                if not user:
                    acc_nums = []
                    # Check for direct destinationAccountNumber
                    direct_acc = data.get("destinationAccountNumber")
                    if direct_acc:
                        acc_nums.append(str(direct_acc).strip())
                    
                    # Check list of destinationAccountPaymentResults
                    dest_results = data.get("destinationAccountPaymentResults") or []
                    for res in dest_results:
                        if isinstance(res, dict):
                            num = res.get("destinationAccountNumber")
                            if num:
                                acc_nums.append(str(num).strip())
                    
                    for acc_num in acc_nums:
                        if acc_num:
                            va = db.query(VirtualAccount).filter(
                                VirtualAccount.account_number == acc_num,
                                VirtualAccount.provider == VirtualAccountProvider.MONNIFY
                            ).first()
                            if va:
                                user = va.user
                                if user:
                                    logger.info("Monnify Webhook: Resolved user ID %d from VirtualAccount table by account number %s", user.id, acc_num)
                                    break

                # 3. Fallback: Resolve user by email
                if not user:
                    customer = data.get("customer") or {}
                    email = (customer.get("email") or "").strip().lower()
                    if email:
                        user = db.query(User).filter(User.email == email).first()
                        if user:
                            logger.info("Monnify Webhook: Resolved user ID %d by email %s as fallback", user.id, email)

                if user:
                    if ext_ref and (existing := db.query(Transaction).filter(Transaction.external_reference == ext_ref).first()):
                        _maybe_reward_first_deposit(
                            db,
                            user=existing.user,
                            reference=existing.reference,
                            amount=Decimal(existing.amount),
                            status=existing.status,
                        )
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
                        desc = f"Wallet funding from {sender_name}" if sender_name else "Wallet funding via bank transfer (Monnify)"
                        credit_wallet(db, wallet, amt, tx_ref, desc, sender_name=sender_name)
                        db.commit()
                        _maybe_reward_first_deposit(
                            db,
                            user=user,
                            reference=tx_ref,
                            amount=amt,
                            status=TransactionStatus.SUCCESS,
                        )
                    except IntegrityError:
                        db.rollback()
                        return {"status": "ok"}
                else:
                    logger.warning("Monnify Webhook: Could not resolve user for transaction %s. Account Ref: %s, Email: %s", ext_ref, account_ref, data.get("customer", {}).get("email"))

    if payment_status in {"FAILED", "CANCELLED"} or (event_type and "FAILED" in event_type):
        if transaction and transaction.status == TransactionStatus.PENDING:
            transaction.status = TransactionStatus.FAILED
            transaction.external_reference = data.get("transactionReference") or data.get("paymentReference")
            db.commit()

    return {"status": "ok"}


@router.post("/billstack")
async def billstack_webhook(request: Request, db: Session = Depends(get_db)):
    signature = request.headers.get("x-billstack-signature") or request.headers.get("billstack-signature")
    body = await request.body()
    
    # Verify signature only if billstack_webhook_secret is explicitly configured
    if settings.billstack_webhook_secret:
        if not signature or not verify_billstack_signature(body, signature):
            logger.error("Billstack Webhook: Invalid signature")
            raise HTTPException(status_code=401, detail="Invalid signature")
    elif signature:
        is_valid = verify_billstack_signature(body, signature)
        logger.info("Billstack Webhook: Signature present. Verification result: %s", is_valid)

    payload = await request.json()
    logger.info("Billstack Webhook received: %s", payload)

    event = payload.get("event")
    event_upper = str(event or "").upper().strip()
    data = payload.get("eventData", {}) or payload.get("data", {})
    
    if event_upper not in {"PAYMENT_NOTIFICATION", "PAYMENT_NOTIFIFICATION"}:
        logger.info("Billstack Webhook: Ignored event type %s", event)
        return {"status": "ignored"}
        
    tx_type = str(data.get("type") or "").upper().strip()
    if tx_type != "RESERVED_ACCOUNT_TRANSACTION":
        logger.info("Billstack Webhook: Ignored transaction type %s", tx_type)
        return {"status": "ignored"}

    reference = data.get("reference")
    merchant_reference = data.get("merchant_reference")
    amount_str = data.get("amount")

    if not amount_str:
        logger.warning("Billstack Webhook: Missing amount")
        return {"status": "ignored"}

    amount = Decimal(str(amount_str))

    payer = data.get("payer")
    if isinstance(payer, dict):
        first_name = str(payer.get("first_name") or "").strip()
        last_name = str(payer.get("last_name") or "").strip()
        sender_name = f"{first_name} {last_name}".strip()
    else:
        sender_name = None
    if not sender_name:
        sender_name = None

    # Resolve User:
    # 1. Parse user_id from merchant_reference (which would be of format "AXISVTU_{user_id}_billstack_...")
    user = None
    if merchant_reference:
        ref_str = str(merchant_reference).strip()
        if ref_str.startswith("AXISVTU_"):
            try:
                user_id = int(ref_str.split("_")[1])
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    logger.info("Billstack Webhook: Resolved user ID %d directly from merchant_reference %s", user.id, ref_str)
            except (ValueError, IndexError):
                pass
        
        if not user:
            # Query VirtualAccount table by customer_reference / reservation_reference
            va = db.query(VirtualAccount).filter(
                (VirtualAccount.reservation_reference == ref_str) |
                (VirtualAccount.customer_reference == ref_str)
            ).first()
            if va:
                user = va.user
                if user:
                    logger.info("Billstack Webhook: Resolved user ID %d from VirtualAccount by merchant_reference %s", user.id, ref_str)

    # 2. Resolve user by destination account number (from data["account"]["account_number"])
    if not user:
        account_data = data.get("account") or {}
        acc_num = account_data.get("account_number")
        if acc_num:
            va = db.query(VirtualAccount).filter(
                VirtualAccount.account_number == str(acc_num).strip(),
                VirtualAccount.provider == VirtualAccountProvider.BILLSTACK
            ).first()
            if va:
                user = va.user
                if user:
                    logger.info("Billstack Webhook: Resolved user ID %d from VirtualAccount table by account number %s", user.id, acc_num)

    # 3. Fallback: Resolve user by email
    if not user:
        email = payload.get("meta", {}).get("email") or payload.get("customer", {}).get("email")
        if email:
            user = db.query(User).filter(User.email == str(email).strip().lower()).first()
            if user:
                logger.info("Billstack Webhook: Resolved user ID %d by email %s as fallback", user.id, email)

    if not user:
        logger.warning("Billstack Webhook: Could not resolve user for transaction %s. Merchant Reference: %s", reference, merchant_reference)
        return {"status": "ignored"}

    # Unique check via external reference (billstack reference)
    if reference and (existing := db.query(Transaction).filter(Transaction.external_reference == reference).first()):
        _maybe_reward_first_deposit(
            db,
            user=existing.user,
            reference=existing.reference,
            amount=Decimal(existing.amount),
            status=existing.status,
        )
        return {"status": "ok"}

    tx_ref = _safe_ref("TRF", str(reference or secrets.token_hex(8)))
    tx = Transaction(
        user_id=user.id,
        reference=tx_ref,
        amount=amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
        external_reference=str(reference) if reference else None,
    )
    try:
        db.add(tx)
        wallet = get_or_create_wallet(db, user.id)
        desc = f"Wallet funding from {sender_name}" if sender_name else "Wallet funding via bank transfer (Billstack)"
        credit_wallet(db, wallet, amount, tx_ref, desc, sender_name=sender_name)
        db.commit()
        _maybe_reward_first_deposit(
            db,
            user=user,
            reference=tx_ref,
            amount=amount,
            status=TransactionStatus.SUCCESS,
        )
        logger.info("Billstack Webhook: Successfully credited user ID %d with NGN %s (Ref: %s)", user.id, amount, tx_ref)
    except IntegrityError:
        db.rollback()
        return {"status": "ok"}

    return {"status": "ok"}

