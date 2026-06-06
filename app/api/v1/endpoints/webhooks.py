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
from app.core.config import get_settings
from app.api.v1.endpoints.wallet import _maybe_reward_first_deposit, _safe_ref

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
                wallet = get_or_create_wallet(db, transaction.user_id)
                credit_wallet(db, wallet, Decimal(transaction.amount), reference, "Wallet funding via Monnify")
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
            customer = data.get("customer") or {}
            email = (customer.get("email") or "").strip().lower()
            amount_paid = data.get("amountPaid") or data.get("amount") or data.get("amountPaidInKobo")
            ext_ref = data.get("transactionReference") or data.get("paymentReference") or reference
            if email and amount_paid:
                user = db.query(User).filter(User.email == email).first()
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
                        credit_wallet(db, wallet, amt, tx_ref, "Wallet funding via bank transfer (Monnify)")
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

    if payment_status in {"FAILED", "CANCELLED"} or (event_type and "FAILED" in event_type):
        if transaction and transaction.status == TransactionStatus.PENDING:
            transaction.status = TransactionStatus.FAILED
            transaction.external_reference = data.get("transactionReference") or data.get("paymentReference")
            db.commit()

    return {"status": "ok"}

