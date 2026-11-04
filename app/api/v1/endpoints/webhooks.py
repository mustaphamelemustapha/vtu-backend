import logging
import json
import hashlib
import hmac
import os
import re
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from decimal import Decimal

from datetime import datetime, timezone
import secrets
from sqlalchemy.exc import IntegrityError
from app.core.database import get_db
from app.models import Transaction, TransactionStatus, TransactionType, User, WalletLedger, LedgerType, DataPlan
from app.models.integration import PaymentGateway
from app.services.wallet import get_or_create_wallet, credit_wallet
from app.services.monnify import verify_monnify_signature
from app.services.billstack import verify_billstack_signature
from app.services.outbound_webhooks import dispatch_developer_webhook
from app.core.config import get_settings
from app.api.v1.endpoints.wallet import _maybe_reward_first_deposit, _safe_ref, _resolve_paystack_transfer_user
from app.models.virtual_account import VirtualAccount, VirtualAccountProvider, VirtualAccountStatus
from app.services.paystack import verify_paystack_signature
from app.services.push_notification import PushNotificationService
from app.core.tenant import is_meledata_tenant

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()

def _tx_age_seconds(tx: Transaction) -> float:
    created = getattr(tx, "created_at", None)
    if not created:
        return 9999.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (now - created).total_seconds())

@router.post("/vtpass")
@router.post("/vtpass/")
@router.post("/vtpass/webhook")
@router.post("/vtpass/webhook/")
async def vtpass_webhook(request: Request, db: Session = Depends(get_db)):
    """
    VTPass Callback/Webhook endpoint.
    VTPass sends transaction status updates here.
    """
    try:
        payload = await request.json()
        logger.info(f"VTPass Webhook received: {payload}")
        
        # VTpass typically sends request_id and content.transactions.status
        request_id = payload.get("requestId")
        if not request_id:
            return {"status": "ignored", "reason": "No requestId provided"}
            
        content = payload.get("content", {})
        transactions = content.get("transactions", {})
        status = str(transactions.get("status") or "").lower()
        
        transaction = db.query(Transaction).filter(Transaction.reference == request_id).first()
        if not transaction:
            return {"status": "ignored", "reason": "Transaction not found"}
            
        if status in {"delivered", "successful"}:
            if transaction.status != TransactionStatus.SUCCESS:
                transaction.status = TransactionStatus.SUCCESS
                db.commit()
                # Optionally trigger developer webhooks
                dispatch_developer_webhook(transaction, transaction.user)
                
        elif status in {"failed", "reversed"}:
            if transaction.status == TransactionStatus.PENDING:
                transaction.status = TransactionStatus.FAILED
                transaction.failure_reason = "Failed via VTpass Webhook"
                db.commit()
                # You might want to handle refunds here if applicable
                
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error processing VTpass webhook: {e}")
        # VTPass expects a 200 OK so it doesn't keep retrying unnecessarily
        return {"status": "error", "message": str(e)}

async def get_raw_body(request: Request) -> bytes:
    return await request.body()

@router.post("/paystack")
@router.post("/paystack/")
@router.post("/paystack/webhook")
@router.post("/paystack/webhook/")
def paystack_webhook(request: Request, raw_body: bytes = Depends(get_raw_body), db: Session = Depends(get_db)):
    signature = request.headers.get("x-paystack-signature")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    body = raw_body
    if not verify_paystack_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(raw_body)
    event = payload.get("event")
    data = payload.get("data", {})

    reference = data.get("reference")
    transaction = db.query(Transaction).filter(Transaction.reference == reference).first()

    if event == "charge.success" and transaction:
        if transaction.status != TransactionStatus.SUCCESS:
            auth = data.get("authorization")
            sender_name = auth.get("sender_name") if isinstance(auth, dict) else None
            if sender_name:
                sender_name = str(sender_name).strip()
            else:
                sender_name = None
            wallet = get_or_create_wallet(db, transaction.user_id)
            desc = f"Wallet funding from {sender_name}" if sender_name else "Wallet funding via Paystack"
            credit_wallet(db, wallet, Decimal(transaction.amount), reference, desc, sender_name=sender_name)
            transaction.status = TransactionStatus.SUCCESS
            transaction.external_reference = data.get("id")
            db.commit()
        _maybe_reward_first_deposit(
            db,
            user=db.query(User).filter(User.id == transaction.user_id).first() or transaction.user,
            reference=reference,
            amount=Decimal(transaction.amount),
            status=TransactionStatus.SUCCESS,
        )
    elif event == "charge.success" and not transaction:
        # Dedicated virtual account funding (no internal reference).
        ext_ref = str(data.get("id") or "").strip()
        amount_kobo = data.get("amount")
        user = _resolve_paystack_transfer_user(db, data)
        if user and amount_kobo:
            if ext_ref and (existing := db.query(Transaction).filter(Transaction.external_reference == ext_ref).first()):
                _maybe_reward_first_deposit(
                    db,
                    user=existing.user,
                    reference=existing.reference,
                    amount=Decimal(existing.amount),
                    status=existing.status,
                )
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
                auth = data.get("authorization")
                sender_name = auth.get("sender_name") if isinstance(auth, dict) else None
                if sender_name:
                    sender_name = str(sender_name).strip()
                else:
                    sender_name = None
                wallet = get_or_create_wallet(db, user.id)
                desc = f"Wallet funding from {sender_name}" if sender_name else "Wallet funding via Paystack transfer"
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

    elif event == "transfer.success" and transaction:
        if transaction.status != TransactionStatus.SUCCESS:
            transaction.status = TransactionStatus.SUCCESS
            transaction.external_reference = str(data.get("transfer_code") or data.get("id") or "")
            db.commit()

    elif event in ("transfer.failed", "transfer.reversed") and transaction:
        if transaction.status in (TransactionStatus.PENDING, TransactionStatus.SUCCESS):
            fee = Decimal("25.00")
            if transaction.data_plan_code and transaction.data_plan_code.startswith("FEE_"):
                try:
                    fee = Decimal(transaction.data_plan_code.replace("FEE_", ""))
                except Exception:
                    fee = Decimal("25.00")
            total_refund = Decimal(transaction.amount) + fee
            wallet = get_or_create_wallet(db, transaction.user_id)
            wallet.balance += total_refund

            refund_ref = _safe_ref("REF", transaction.reference)
            refund_ledger = WalletLedger(
                wallet_id=wallet.id,
                amount=total_refund,
                entry_type=LedgerType.CREDIT,
                reference=refund_ref,
                description=f"Refund for failed bank withdrawal #{transaction.reference}",
            )
            db.add(refund_ledger)
            transaction.status = TransactionStatus.FAILED
            transaction.failure_reason = str(data.get("gateway_response") or data.get("reason") or "Transfer failed at bank")[:255]
            transaction.external_reference = str(data.get("transfer_code") or data.get("id") or "")
            db.commit()
            logger.info("Processed %s for withdrawal %s, refunded NGN %s to user %s", event, transaction.reference, total_refund, transaction.user_id)

    return {"status": "ok"}


@router.post("/smeplug")
@router.post("/smeplug/")
@router.post("/smeplug/webhook")
@router.post("/smeplug/webhook/")
def smeplug_webhook(request: Request, raw_body: bytes = Depends(get_raw_body), db: Session = Depends(get_db)):
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

    payload = json.loads(raw_body)
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

    # Provider check: if transaction is handled by another provider (e.g. routed to fallback amigo), ignore
    if transaction.provider and transaction.provider.lower() not in {"smeplug", "sim"}:
        logger.info(
            "SMEPlug Webhook: Transaction %s is handled by %s, not smeplug. Ignoring.",
            customer_reference, transaction.provider
        )
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
        dispatch_developer_webhook(transaction, transaction.user)
    elif status == "failed":
        # Check if plan has fallback configured or if transaction is actively running in data.py
        plan = db.query(DataPlan).filter(DataPlan.plan_code == transaction.data_plan_code).first() if transaction.data_plan_code else None
        has_fallback = bool(plan and plan.fallback_provider and str(plan.fallback_provider).strip().lower() not in {"", "none"})
        age_sec = _tx_age_seconds(transaction)

        if has_fallback:
            logger.warning(
                "SMEPlug Webhook: Transaction %s failed on primary SMEPlug, but fallback (%s) is configured. Skipping premature webhook refund.",
                customer_reference, plan.fallback_provider
            )
            transaction.external_reference = provider_reference
            db.commit()
            return {"status": "ok"}

        if age_sec < 45:
            # Synchronous purchase route in data.py is actively processing. Let data.py handle the outcome.
            logger.warning(
                "SMEPlug Webhook: Transaction %s failed on SMEPlug within %.1fs of creation. Deferring refund to in-flight purchase handler.",
                customer_reference, age_sec
            )
            transaction.external_reference = provider_reference
            db.commit()
            return {"status": "ok"}

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
        dispatch_developer_webhook(transaction, transaction.user)
        
        # Send push notification for refund
        if transaction.user and transaction.user.fcm_token:
            PushNotificationService.send_push_notification(
                token=transaction.user.fcm_token,
                title="Data Purchase Failed",
                body=f"Your Airtel data purchase failed. Your wallet has been refunded ₦{transaction.amount}.",
                data={"transaction_id": transaction.id, "type": "refund"}
            )
    else:
        logger.info("SMEPlug Webhook: Transaction %s status is %s, keeping pending", customer_reference, status)

    return {"status": "ok"}

@router.post("/monnify")
@router.post("/monnify/")
@router.post("/monnify/webhook")
@router.post("/monnify/webhook/")
def monnify_webhook(request: Request, raw_body: bytes = Depends(get_raw_body), db: Session = Depends(get_db)):
    signature = request.headers.get("monnify-signature")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    body = raw_body
    if not verify_monnify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(raw_body)
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
                                VirtualAccount.provider.in_([VirtualAccountProvider.MONNIFY, "monnify", "MONNIFY"])
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
@router.post("/billstack/")
@router.post("/billstack/webhook")
@router.post("/billstack/webhook/")
def billstack_webhook(request: Request, raw_body: bytes = Depends(get_raw_body), db: Session = Depends(get_db)):
    signature = (
        request.headers.get("x-wiaxy-signature")
        or request.headers.get("x-wiaxy-signature-256")
        or request.headers.get("x-billstack-signature")
        or request.headers.get("billstack-signature")
    )
    timestamp = request.headers.get("x-wiaxy-timestamp")
    body = raw_body

    payload = json.loads(raw_body)
    logger.info("Billstack Webhook received: %s", payload)

    event = payload.get("event")
    event_upper = str(event or "").upper().strip()
    data = payload.get("eventData", {}) or payload.get("data", {})

    if settings.billstack_webhook_secret:
        sig_ok = bool(signature and verify_billstack_signature(body, signature, timestamp=timestamp))
        if not sig_ok:
            # Fallback check: verify whether the destination account or reference belongs to a known Billstack VirtualAccount
            acc_num_check = str((data.get("account") or {}).get("account_number") or "").strip()
            m_ref_check = str(data.get("merchant_reference") or "").strip()
            r_ref_check = str(data.get("reference") or "").strip()
            known_va = None
            if acc_num_check:
                known_va = db.query(VirtualAccount).filter(
                    VirtualAccount.account_number == acc_num_check,
                    VirtualAccount.provider.in_([VirtualAccountProvider.BILLSTACK, "billstack", "BILLSTACK"]),
                ).first()
            if not known_va and (m_ref_check or r_ref_check):
                ref_to_match = m_ref_check or r_ref_check
                known_va = db.query(VirtualAccount).filter(
                    (VirtualAccount.customer_reference == ref_to_match)
                    | (VirtualAccount.reservation_reference == ref_to_match)
                    | (VirtualAccount.customer_reference.like(f"{ref_to_match}%"))
                    | (VirtualAccount.reservation_reference.like(f"{ref_to_match}%"))
                ).first()
            if known_va:
                logger.warning(
                    "Billstack Webhook: Signature mismatch (header=%s), but verified known VirtualAccount %s for user_id=%s. Proceeding.",
                    signature,
                    known_va.account_number,
                    known_va.user_id,
                )
            else:
                logger.error("Billstack Webhook: Invalid signature (header=%s)", signature)
                raise HTTPException(status_code=401, detail="Invalid signature")
    elif signature:
        is_valid = verify_billstack_signature(body, signature, timestamp=timestamp)
        logger.info("Billstack Webhook: Signature present. Verification result: %s", is_valid)
    
    if event_upper not in {"PAYMENT_NOTIFICATION", "PAYMENT_NOTIFIFICATION"}:
        logger.info("Billstack Webhook: Ignored event type %s", event)
        return {"status": "ignored"}
        
    tx_type = str(data.get("type") or "").upper().strip()
    if tx_type != "RESERVED_ACCOUNT_TRANSACTION":
        logger.info("Billstack Webhook: Ignored transaction type %s", tx_type)
        return {"status": "ignored"}

    reference = data.get("transaction_ref") or data.get("wiaxy_ref") or data.get("reference")
    merchant_reference = data.get("merchant_reference")
    amount_str = data.get("amount")

    if not amount_str:
        logger.warning("Billstack Webhook: Missing amount")
        return {"status": "ignored"}

    amount = Decimal(str(amount_str))

    payer = data.get("payer")
    if isinstance(payer, list) and len(payer) > 0:
        payer = payer[0]

    sender_name = None
    if isinstance(payer, dict):
        first_name = str(payer.get("first_name") or payer.get("firstName") or "").strip()
        last_name = str(payer.get("last_name") or payer.get("lastName") or "").strip()
        if first_name or last_name:
            sender_name = f"{first_name} {last_name}".strip()
        else:
            sender_name = str(payer.get("account_name") or payer.get("name") or "").strip()

    if not sender_name:
        customer = data.get("customer") or {}
        if isinstance(customer, dict):
            first_name = str(customer.get("firstName") or customer.get("first_name") or "").strip()
            last_name = str(customer.get("lastName") or customer.get("last_name") or "").strip()
            if first_name or last_name:
                sender_name = f"{first_name} {last_name}".strip()
            else:
                sender_name = str(customer.get("name") or "").strip()

    if sender_name:
        sender_name = str(sender_name).strip()
        if "/" in sender_name:
            parts = sender_name.split("/", 1)
            if len(parts) > 1 and parts[1].strip():
                sender_name = parts[1].strip()
    else:
        sender_name = None

    # Resolve User:
    # 1. Parse user_id from merchant_reference (e.g. "KULLOMADATA_{user_id}_billstack_...", "MZDATA_{user_id}_billstack_...", "AXISVTU_{user_id}_...")
    user = None
    if merchant_reference:
        ref_str = str(merchant_reference).strip()
        parts = ref_str.split("_")
        if len(parts) >= 2 and parts[1].isdigit() and (
            ref_str.startswith(("AXISVTU_", "KULLOMADATA_", "MZDATA_", "MELEDATA_"))
            or "billstack" in ref_str.lower()
        ):
            try:
                user_id = int(parts[1])
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    logger.info("Billstack Webhook: Resolved user ID %d directly from merchant_reference %s", user.id, ref_str)
            except (ValueError, IndexError):
                pass
        
        if not user:
            # Query VirtualAccount table by customer_reference / reservation_reference
            va = db.query(VirtualAccount).filter(
                (VirtualAccount.reservation_reference == ref_str) |
                (VirtualAccount.customer_reference == ref_str) |
                (VirtualAccount.reservation_reference.like(f"{ref_str}%")) |
                (VirtualAccount.customer_reference.like(f"{ref_str}%"))
            ).first()
            if va:
                user = va.user
                if user:
                    logger.info("Billstack Webhook: Resolved user ID %d from VirtualAccount by merchant_reference %s", user.id, ref_str)

    if not user and data.get("reference"):
        r_ref = str(data.get("reference")).strip()
        va = db.query(VirtualAccount).filter(
            (VirtualAccount.reservation_reference == r_ref) |
            (VirtualAccount.reservation_reference.like(f"{r_ref}%"))
        ).first()
        if va and va.user:
            user = va.user
            logger.info("Billstack Webhook: Resolved user ID %d from VirtualAccount by reference %s", user.id, r_ref)

    # 2. Resolve user by destination account number (from data["account"]["account_number"])
    if not user:
        account_data = data.get("account") or {}
        acc_num = account_data.get("account_number")
        if acc_num:
            va = db.query(VirtualAccount).filter(
                VirtualAccount.account_number == str(acc_num).strip(),
                VirtualAccount.provider.in_([VirtualAccountProvider.BILLSTACK, "billstack", "BILLSTACK"])
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

    tx_ref = _safe_ref("TRF", str(reference or secrets.token_hex(8)))

    # Unique check via external reference or reference/tx_ref
    transaction = None
    if reference:
        transaction = db.query(Transaction).filter(
            (Transaction.external_reference == reference) | 
            (Transaction.reference == tx_ref)
        ).first()

    if transaction:
        if transaction.status == TransactionStatus.SUCCESS:
            logger.info("Billstack Webhook: Transaction %s already marked SUCCESS. Reference: %s", reference, transaction.reference)
            
            # Verify if a corresponding ledger entry exists to prevent missing credits
            wallet = get_or_create_wallet(db, transaction.user_id)
            ledger = db.query(WalletLedger).filter(
                WalletLedger.wallet_id == wallet.id,
                WalletLedger.reference == transaction.reference,
                WalletLedger.entry_type == LedgerType.CREDIT
            ).first()
            
            if not ledger:
                logger.warning("Billstack Webhook: Transaction is SUCCESS but no ledger entry found. Crediting wallet now. Reference: %s", transaction.reference)
                try:
                    desc = f"Wallet funding from {sender_name}" if sender_name else "Wallet funding via bank transfer (Billstack)"
                    credit_wallet(db, wallet, Decimal(transaction.amount), transaction.reference, desc, sender_name=sender_name)
                    db.commit()
                    logger.info("Billstack Webhook: Successfully credited missing wallet amount NGN %s for SUCCESS transaction %s", transaction.amount, transaction.reference)
                except Exception as e:
                    db.rollback()
                    logger.error("Billstack Webhook: Error crediting missing wallet amount for transaction %s: %s", transaction.reference, e)
            
            _maybe_reward_first_deposit(
                db,
                user=transaction.user,
                reference=transaction.reference,
                amount=Decimal(transaction.amount),
                status=transaction.status,
            )
            return {"status": "ok"}
        else:
            logger.info("Billstack Webhook: Found existing non-success transaction %s. Crediting wallet now.", transaction.reference)
            try:
                wallet = get_or_create_wallet(db, transaction.user_id)
                desc = f"Wallet funding from {sender_name}" if sender_name else "Wallet funding via bank transfer (Billstack)"
                credit_wallet(db, wallet, Decimal(transaction.amount), transaction.reference, desc, sender_name=sender_name)
                transaction.status = TransactionStatus.SUCCESS
                if not transaction.external_reference and reference:
                    transaction.external_reference = reference
                db.commit()
                _maybe_reward_first_deposit(
                    db,
                    user=transaction.user,
                    reference=transaction.reference,
                    amount=Decimal(transaction.amount),
                    status=TransactionStatus.SUCCESS,
                )
                logger.info("Billstack Webhook: Successfully updated and credited existing transaction user ID %d with NGN %s (Ref: %s)", transaction.user_id, transaction.amount, transaction.reference)
            except Exception as e:
                db.rollback()
                logger.error("Billstack Webhook: Error crediting existing transaction %s: %s", transaction.reference, e)
            return {"status": "ok"}

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
    except IntegrityError as e:
        db.rollback()
        logger.error("Billstack Webhook: IntegrityError when crediting new transaction for user ID %d, reference %s: %s", user.id, reference, e)
        return {"status": "ok"}
    except Exception as e:
        db.rollback()
        logger.error("Billstack Webhook: Unexpected error when crediting new transaction for user ID %d, reference %s: %s", user.id, reference, e)
        return {"status": "ok"}

    return {"status": "ok"}


@router.post("/autosync")
@router.post("/autosync/")
@router.post("/autosync/webhook")
@router.post("/autosync/webhook/")
def autosync_webhook(request: Request, raw_body: bytes = Depends(get_raw_body), db: Session = Depends(get_db)):
    """
    Autosync Webhook Handler
    """
    payload = json.loads(raw_body)
    logger.info("Autosync Webhook received: %s", payload)
    
    received_hash = payload.get("hash")
    tx_data = payload.get("transaction") or {}
    
    reference = tx_data.get("reference")
    if not reference:
        logger.warning("Autosync Webhook missing transaction reference")
        return "error"
        
    pin = str(settings.autosync_webhook_pin or "").strip()
    if not pin:
        logger.warning("Autosync webhook PIN not configured")
        return "error"
        
    expected_hash_string = f"{pin}:{reference}"
    expected_hash = hashlib.sha256(expected_hash_string.encode('utf-8')).hexdigest()
    
    if received_hash != expected_hash:
        logger.warning("Autosync Webhook: Invalid hash for ref %s", reference)
        return "error"
        
    request_ref = tx_data.get("request_ref")
    
    transaction = db.query(Transaction).filter(Transaction.reference == request_ref).first()
    if not transaction:
        transaction = db.query(Transaction).filter(Transaction.external_reference == reference).first()
        
    if not transaction:
        logger.warning("Autosync Webhook: Transaction not found for request_ref %s and ref %s", request_ref, reference)
        return "error"
        
    if transaction.status in {TransactionStatus.SUCCESS, TransactionStatus.REFUNDED}:
        logger.info("Autosync Webhook: Transaction %s already in terminal state %s", reference, transaction.status)
        return "ok"
        
    # Provider check: if transaction was rerouted to a different provider, ignore
    if transaction.provider and transaction.provider.lower() != "autosync":
        logger.info("Autosync Webhook: Transaction %s is handled by %s, not autosync. Ignoring.", reference, transaction.provider)
        return "ok"

    status = str(tx_data.get("status") or "").strip().lower()
    
    if status == "successful":
        transaction.status = TransactionStatus.SUCCESS
        if tx_data.get("provider_reference"):
            transaction.external_reference = tx_data.get("provider_reference")
            
        # Extract token for electricity purchases
        token = str(tx_data.get("token") or tx_data.get("pin") or "")
        units = str(tx_data.get("units") or "")
        if token or units:
            meta = transaction.meta or {}
            if token: meta["token"] = token
            if units: meta["units"] = units
            transaction.meta = meta
            
        db.commit()
        
        try:
            from app.services.referrals import trigger_referral_data_activity
            trigger_referral_data_activity(db, transaction)
            db.commit()
        except Exception as ref_exc:
            logger.error("Failed to trigger referral activity in webhook: %s", ref_exc)
            
        logger.info("Autosync Webhook: Transaction %s marked as SUCCESS", reference)
        dispatch_developer_webhook(transaction, transaction.user)
        
    elif status == "failed":
        plan = db.query(DataPlan).filter(DataPlan.plan_code == transaction.data_plan_code).first() if transaction.data_plan_code else None
        has_fallback = bool(plan and plan.fallback_provider and str(plan.fallback_provider).strip().lower() not in {"", "none"})
        age_sec = _tx_age_seconds(transaction)

        if has_fallback or age_sec < 45:
            logger.warning(
                "Autosync Webhook: Transaction %s failed on Autosync, but has_fallback=%s (age=%.1fs). Skipping premature refund.",
                reference, has_fallback, age_sec
            )
            if tx_data.get("provider_reference"):
                transaction.external_reference = tx_data.get("provider_reference")
            db.commit()
            return "ok"

        transaction.status = TransactionStatus.FAILED
        if tx_data.get("provider_reference"):
            transaction.external_reference = tx_data.get("provider_reference")
            
        wallet = get_or_create_wallet(db, transaction.user_id)
        service_name = getattr(transaction, "type", "transaction").capitalize()
        
        credit_wallet(
            db, 
            wallet, 
            Decimal(transaction.amount), 
            transaction.reference, 
            f"Refund for failed Autosync {service_name} purchase (Ref: {reference})"
        )
        transaction.status = TransactionStatus.REFUNDED
        db.commit()
        logger.info("Autosync Webhook: Transaction %s marked as FAILED and REFUNDED", reference)
        dispatch_developer_webhook(transaction, transaction.user)
        
        if transaction.user and transaction.user.fcm_token:
            PushNotificationService.send_push_notification(
                token=transaction.user.fcm_token,
                title=f"{service_name} Purchase Failed",
                body=f"Your {service_name.lower()} purchase failed. Your wallet has been refunded ₦{transaction.amount}.",
                data={"transaction_id": transaction.id, "type": "refund"}
            )
            
    return "ok"

@router.post("/boltnet")
@router.post("/boltnet/")
@router.post("/boltnet/webhook")
@router.post("/boltnet/webhook/")
def boltnet_webhook(request: Request, raw_body: bytes = Depends(get_raw_body), db: Session = Depends(get_db)):
    """
    Boltnet Webhook Handler
    """
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return "invalid json"
        
    logger.info("Boltnet Webhook received: %s", payload)
    
    # Payload should include request_id and ident
    request_id = payload.get("request_id")
    ident = payload.get("ident") or payload.get("id")
    
    if not request_id:
        logger.warning("Boltnet Webhook missing request_id")
        return "error"
        
    transaction = db.query(Transaction).filter(Transaction.reference == request_id).first()
    if not transaction:
        logger.warning("Boltnet Webhook: Transaction not found for request_id %s", request_id)
        return "error"
        
    if transaction.status in {TransactionStatus.SUCCESS, TransactionStatus.REFUNDED}:
        logger.info("Boltnet Webhook: Transaction %s already in terminal state %s", request_id, transaction.status)
        return "ok"

    # Provider check: if transaction was rerouted to a different provider, ignore
    if transaction.provider and transaction.provider.lower() != "boltnet":
        logger.info("Boltnet Webhook: Transaction %s is handled by %s, not boltnet. Ignoring.", request_id, transaction.provider)
        return "ok"
        
    status = str(payload.get("Status") or payload.get("status") or "").strip().lower()
    
    if status in {"successful", "success", "delivered"}:
        transaction.status = TransactionStatus.SUCCESS
        if ident:
            transaction.external_reference = str(ident)
            
        db.commit()
        
        try:
            from app.services.referrals import trigger_referral_data_activity
            trigger_referral_data_activity(db, transaction)
            db.commit()
        except Exception as ref_exc:
            logger.error("Failed to trigger referral activity in boltnet webhook: %s", ref_exc)
            
        logger.info("Boltnet Webhook: Transaction %s marked as SUCCESS", request_id)
        dispatch_developer_webhook(transaction, transaction.user)
        
    elif status in {"failed", "reversed", "declined"}:
        plan = db.query(DataPlan).filter(DataPlan.plan_code == transaction.data_plan_code).first() if transaction.data_plan_code else None
        has_fallback = bool(plan and plan.fallback_provider and str(plan.fallback_provider).strip().lower() not in {"", "none"})
        age_sec = _tx_age_seconds(transaction)

        if has_fallback or age_sec < 45:
            logger.warning(
                "Boltnet Webhook: Transaction %s failed on Boltnet, but has_fallback=%s (age=%.1fs). Skipping premature refund.",
                request_id, has_fallback, age_sec
            )
            if ident:
                transaction.external_reference = str(ident)
            db.commit()
            return "ok"

        transaction.status = TransactionStatus.FAILED
        if ident:
            transaction.external_reference = str(ident)
            
        wallet = get_or_create_wallet(db, transaction.user_id)
        service_name = getattr(transaction, "tx_type", getattr(TransactionType, "DATA")).name.capitalize()
        
        credit_wallet(
            db, 
            wallet, 
            Decimal(transaction.amount), 
            transaction.reference, 
            f"Refund for failed Boltnet {service_name} purchase (Ref: {request_id})"
        )
        transaction.status = TransactionStatus.REFUNDED
        db.commit()
        logger.info("Boltnet Webhook: Transaction %s marked as FAILED and REFUNDED", request_id)
        dispatch_developer_webhook(transaction, transaction.user)
        
        if transaction.user and transaction.user.fcm_token:
            PushNotificationService.send_push_notification(
                token=transaction.user.fcm_token,
                title=f"{service_name} Purchase Failed",
                body=f"Your {service_name.lower()} purchase failed. Your wallet has been refunded ₦{transaction.amount}.",
                data={"transaction_id": transaction.id, "type": "refund"}
            )
            
    return "ok"

def _parse_safe_deposit_amount(val) -> Decimal:
    if val is None:
        return Decimal("0")
    cleaned = str(val).replace(",", "").replace("₦", "").replace("NGN", "").strip()
    try:
        parsed = Decimal(cleaned)
        return parsed if parsed > Decimal("0") else Decimal("0")
    except Exception:
        return Decimal("0")

@router.get("/aspfiy")
@router.get("/aspfiy/")
@router.get("/aspify")
@router.get("/aspify/")
@router.get("/asfiy")
@router.get("/asfiy/")
def aspfiy_webhook_health():
    return {"status": "active", "provider": "aspfiy"}

@router.post("/aspfiy")
@router.post("/aspfiy/")
@router.post("/aspify")
@router.post("/aspify/")
@router.post("/asfiy")
@router.post("/asfiy/")
async def aspfiy_webhook(request: Request, raw_body: bytes = Depends(get_raw_body), db: Session = Depends(get_db)):
    headers_dict = dict(request.headers)
    logger.info("Aspfiy webhook incoming headers: %s", headers_dict)

    # 1. Early Tenant Guard: Reject if request is scoped to MELE DATA
    if is_meledata_tenant(request):
        logger.info("Aspfiy webhook: Decommissioned for MELE DATA tenant (host=%s)", request.headers.get("host"))
        return {"status": "decommissioned", "message": "Aspfiy integration is decommissioned for MELE DATA"}

    # Legacy proxy: If request hits onrender.com or legacy vercel domain, forward directly to production Coolify (api.meledata.ng)
    host_header = (request.headers.get("host") or "").lower()
    is_legacy_host = any(h in host_header for h in ("onrender.com", "vercel.app", "axisvtu"))
    forward_target = os.environ.get("FORWARD_ASPIFY_WEBHOOK_TO") or (
        "https://api.meledata.ng/api/v1/webhooks/aspfiy" if is_legacy_host else None
    )
    if forward_target and not host_header.startswith("api.meledata.ng"):
        logger.info("Forwarding Aspfiy webhook from legacy host %s to %s", host_header, forward_target)
        try:
            import httpx
            from fastapi.responses import Response
            fwd_headers = {
                k: v for k, v in headers_dict.items()
                if k.lower() not in ("host", "content-length", "transfer-encoding")
            }
            fwd_headers["x-forwarded-from-host"] = host_header
            async with httpx.AsyncClient(timeout=20.0) as client:
                fwd_resp = await client.post(
                    forward_target,
                    content=raw_body,
                    headers=fwd_headers,
                    params=dict(request.query_params)
                )
            logger.info("Aspfiy webhook forwarded to Coolify, response status: %s", fwd_resp.status_code)
            return Response(content=fwd_resp.content, status_code=fwd_resp.status_code, media_type="application/json")
        except Exception as fwd_err:
            logger.error("Failed to forward Aspfiy webhook to %s: %s; falling back to local processing", forward_target, fwd_err)

    # 1. Resolve secret key from DB (Admin Integrations) or Environment
    db_gw = db.query(PaymentGateway).filter(
        PaymentGateway.identifier.in_(["aspfiy", "asfiy", "aspify"])
    ).first()
    db_secret = (db_gw.secret_key or "").strip() if db_gw else ""

    env_secret = (
        settings.aspfiy_secret_key
        or getattr(settings, "aspify_secret_key", "")
        or os.environ.get("ASPFIY_SECRET_KEY", "")
        or os.environ.get("ASPIFY_SECRET_KEY", "")
    ).strip()

    secret_key = db_secret or env_secret

    # Look for signature in all possible header variations & query params
    signature = (
        request.headers.get("x-wiaxy-signature")
        or request.headers.get("x-aspfiy-signature")
        or request.headers.get("x-aspify-signature")
        or request.headers.get("x-signature")
        or request.headers.get("signature")
        or request.headers.get("wiaxy-signature")
        or request.headers.get("x-webhook-signature")
        or request.headers.get("authorization")
        or request.query_params.get("signature")
        or request.query_params.get("token")
        or ""
    ).strip().lower()

    if signature.startswith("bearer "):
        signature = signature[7:].strip()

    valid_signatures = set()
    for candidate_key in {secret_key, db_secret, env_secret}:
        candidate_key = candidate_key.strip()
        if not candidate_key:
            continue
        valid_signatures.add(candidate_key.lower())
        valid_signatures.add(hashlib.md5(candidate_key.encode()).hexdigest().lower())
        valid_signatures.add(hashlib.sha256(candidate_key.encode()).hexdigest().lower())
        try:
            valid_signatures.add(hmac.new(candidate_key.encode(), raw_body, hashlib.sha256).hexdigest().lower())
            valid_signatures.add(hmac.new(candidate_key.encode(), raw_body, hashlib.sha512).hexdigest().lower())
            valid_signatures.add(hashlib.md5((candidate_key + raw_body.decode(errors="ignore")).encode()).hexdigest().lower())
        except Exception:
            pass

    is_valid = bool(signature and signature in valid_signatures)

    try:
        payload = json.loads(raw_body)
    except Exception as parse_err:
        logger.error("Aspfiy webhook: invalid JSON received (%s): %s", parse_err, raw_body[:200])
        return {"status": "error", "message": "invalid JSON"}

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    account_data = data.get("account") if isinstance(data.get("account"), dict) else {}
    customer_data = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    payer_data = data.get("payer") if isinstance(data.get("payer"), dict) else {}

    # Comprehensive candidate extraction
    account_candidates = [
        str(account_data.get("account_number") or ""),
        str(account_data.get("accountNumber") or ""),
        str(data.get("account_number") or ""),
        str(data.get("accountNumber") or ""),
        str(data.get("account_no") or ""),
        str(data.get("accountNo") or ""),
        str(data.get("paid_into") or ""),
        str(data.get("paidInto") or ""),
        str(data.get("destination_account") or ""),
        str(data.get("destination_account_number") or ""),
        str(data.get("virtual_account") or ""),
        str(data.get("virtual_account_number") or ""),
        str(data.get("recipient_account") or ""),
        str(data.get("bank_account") or ""),
        str(payload.get("account_number") or ""),
        str(payload.get("paid_into") or ""),
        str(data.get("account") if isinstance(data.get("account"), str) else ""),
    ]
    clean_accs = [re.sub(r"\D", "", a) for a in account_candidates if a and re.sub(r"\D", "", a)]

    ref_candidates = [
        str(data.get("merchant_reference") or ""),
        str(data.get("merchantReference") or ""),
        str(data.get("reference") or ""),
        str(data.get("customer_reference") or ""),
        str(data.get("customerReference") or ""),
        str(data.get("account_reference") or ""),
        str(data.get("accountReference") or ""),
        str(data.get("payment_reference") or ""),
        str(data.get("transaction_ref") or ""),
        str(data.get("trans_ref") or ""),
        str(data.get("external_reference") or ""),
        str(data.get("externalReference") or ""),
        str(payload.get("merchant_reference") or ""),
        str(payload.get("reference") or ""),
    ]
    ref_candidates = [r.strip() for r in ref_candidates if r and r.strip()]

    # 2. Reference Prefix Guard: Reject if incoming payload targets a MELE DATA reference
    for ref in ref_candidates:
        if re.search(r'(?:MELEDATA|AXISVTU)[_-]', ref, re.IGNORECASE):
            logger.info("Aspfiy webhook: Dropping MELE DATA payload reference '%s'", ref)
            return {"status": "decommissioned", "message": "Aspfiy integration is decommissioned for MELE DATA"}

    email_candidates = [
        str(customer_data.get("email") or ""),
        str(data.get("email") or ""),
        str(payer_data.get("email") or ""),
        str(data.get("customer_email") or ""),
        str(data.get("customerEmail") or ""),
        str(payload.get("email") or ""),
    ]
    email_candidates = [e.strip().lower() for e in email_candidates if e and e.strip() and "@" in e]

    # Resolve account and user
    account = None
    user = None

    # 1. Match by customer_reference on VirtualAccount
    for ref in ref_candidates:
        account = db.query(VirtualAccount).filter(
            VirtualAccount.customer_reference == ref,
            VirtualAccount.provider.in_([VirtualAccountProvider.ASPFIY, "aspfiy", "ASPFIY", "aspify", "ASPIFY"])
        ).first()
        if not account:
            account = db.query(VirtualAccount).filter(
                VirtualAccount.customer_reference == ref
            ).first()
        if account:
            user = db.query(User).filter(User.id == account.user_id).first()
            if user:
                break

    # 2. Match by account_number on VirtualAccount
    if not user:
        for acc_num in clean_accs:
            account = db.query(VirtualAccount).filter(
                VirtualAccount.account_number == acc_num,
                VirtualAccount.provider.in_([VirtualAccountProvider.ASPFIY, "aspfiy", "ASPFIY", "aspify", "ASPIFY"])
            ).first()
            if not account:
                account = db.query(VirtualAccount).filter(
                    VirtualAccount.account_number == acc_num
                ).first()
            if account:
                user = db.query(User).filter(User.id == account.user_id).first()
                if user:
                    break

    # 3. Account Reference Guard: If matched account belongs to MELE DATA, drop
    if account and (
        (account.customer_reference and re.search(r'(?:MELEDATA|AXISVTU)[_-]', account.customer_reference, re.IGNORECASE))
        or (account.reservation_reference and re.search(r'(?:MELEDATA|AXISVTU)[_-]', account.reservation_reference, re.IGNORECASE))
    ):
        logger.info("Aspfiy webhook: Matched account %s belongs to MELE DATA; dropping payment", account.account_number)
        return {"status": "decommissioned", "message": "Aspfiy integration is decommissioned for MELE DATA"}

    # 3. Match from user ID inside reference patterns (e.g. AXISVTU_123_..., MELEDATA_123_..., 123_aspfiy_...)
    if not user:
        for ref in ref_candidates:
            m = re.search(r'(?:AXISVTU|MELEDATA|KULLOMA|MZDATA|VTU|USER)[_-](\d+)', ref, re.IGNORECASE)
            if not m:
                m = re.search(r'^(\d+)[_-]', ref)
            if m:
                try:
                    uid = int(m.group(1))
                    user = db.query(User).filter(User.id == uid).first()
                    if user:
                        break
                except Exception:
                    pass

    # 4. Match by customer email
    if not user:
        for em in email_candidates:
            user = db.query(User).filter(User.email == em).first()
            if user:
                break

    # Fallback verification:
    if not is_valid:
        if account or user:
            logger.info(
                "Aspfiy webhook: Accepted via account/user match (accs=%s, refs=%s, emails=%s) despite signature verification.",
                clean_accs[:2], ref_candidates[:2], email_candidates[:1]
            )
        else:
            expected_md5 = hashlib.md5(secret_key.encode()).hexdigest().lower() if secret_key else "NOT_CONFIGURED"
            logger.warning(
                "Aspfiy webhook signature mismatch! Received: '%s', Expected MD5: '%s'. Headers: %s",
                signature,
                expected_md5,
                headers_dict,
            )
            raise HTTPException(
                status_code=401,
                detail=f"Invalid signature (received '{signature[:8]}...', expected MD5 '{expected_md5[:8]}...'). Check ASPFIY_SECRET_KEY."
            )

    # Event Validation: Only skip if explicitly failed / reversed
    event_str = str(
        payload.get("event")
        or data.get("event")
        or payload.get("type")
        or data.get("type")
        or payload.get("status")
        or data.get("status")
        or ""
    ).strip().lower()

    explicit_failures = ("fail", "cancelled", "canceled", "reversed", "declined", "rejected", "error")
    if event_str and any(fail_word in event_str for fail_word in explicit_failures):
        logger.info("Aspfiy webhook: ignored explicit failure event '%s'", event_str)
        return {"status": "ignored", "message": f"ignored event: {event_str}"}

    # Safe amount extraction
    amount_candidates = [
        data.get("amount"),
        payload.get("amount"),
        data.get("amount_paid"),
        data.get("amountPaid"),
        data.get("settlement_amount"),
        data.get("settlementAmount"),
        data.get("total_amount"),
        data.get("paid_amount"),
        data.get("value"),
    ]
    amount = Decimal("0")
    for cand in amount_candidates:
        if cand is not None:
            parsed = _parse_safe_deposit_amount(cand)
            if parsed > Decimal("0"):
                amount = parsed
                break

    if amount <= Decimal("0"):
        logger.warning("Aspfiy webhook: missing or zero amount in payload: %s", payload)
        return {"status": "ignored", "message": "zero or missing amount"}

    # Unique transaction reference
    tx_ref_candidates = [
        str(data.get("reference") or ""),
        str(data.get("payment_reference") or ""),
        str(data.get("transaction_id") or ""),
        str(data.get("transactionId") or ""),
        str(data.get("transaction_ref") or ""),
        str(data.get("trans_ref") or ""),
        str(data.get("aspfiy_ref") or ""),
        str(data.get("wiaxy_ref") or ""),
        str(data.get("id") or ""),
        str(payload.get("reference") or ""),
        str(payload.get("transaction_reference") or ""),
    ]
    tx_ref_candidates = [r.strip() for r in tx_ref_candidates if r and r.strip()]
    reference = tx_ref_candidates[0] if tx_ref_candidates else f"asp_{secrets.token_hex(8)}"

    acc_num = clean_accs[0] if clean_accs else ""
    best_merchant_ref = ref_candidates[0] if ref_candidates else f"AXISVTU_{user.id}_aspfiy_paga" if user else None

    # Auto-register VirtualAccount if user matched but account record missing
    if user and not account:
        logger.info("Aspfiy webhook: matched user %s; creating virtual account record (acc=%s)", user.id, acc_num)
        account = VirtualAccount(
            user_id=user.id,
            provider=VirtualAccountProvider.ASPFIY,
            account_number=acc_num or "paga",
            account_name=user.full_name or "Mele Data Customer",
            bank_name="Paga",
            bank_code="000",
            customer_reference=best_merchant_ref or f"AXISVTU_{user.id}_aspfiy_paga",
            reservation_reference=reference,
            status=VirtualAccountStatus.ACTIVE,
        )
        db.add(account)
        db.commit()
    elif account and not account.account_number and acc_num:
        account.account_number = acc_num
        db.commit()

    if not user:
        logger.error("Aspfiy webhook: account/user not found for refs=%s accs=%s emails=%s", ref_candidates, clean_accs, email_candidates)
        return {"status": "error", "message": "account not found"}

    # Idempotency check with status recovery
    existing_tx = db.query(Transaction).filter(Transaction.reference == reference).first()
    if existing_tx:
        if existing_tx.status == TransactionStatus.SUCCESS:
            logger.info("Aspfiy webhook: duplicate successful transaction ref=%s", reference)
            return {"status": "success", "message": "duplicate transaction"}
        else:
            logger.info("Aspfiy webhook: upgrading non-successful transaction ref=%s to SUCCESS", reference)
            existing_tx.status = TransactionStatus.SUCCESS
            existing_tx.amount = amount
            db.commit()
            db.refresh(existing_tx)
            tx = existing_tx
    else:
        sender_name = str(
            data.get("sender_name")
            or data.get("payer_name")
            or customer_data.get("name")
            or account_data.get("account_name")
            or payer_data.get("name")
            or "Aspfiy Transfer"
        ).strip()

        # Create Transaction record
        tx = Transaction(
            user_id=user.id,
            reference=reference,
            amount=amount,
            status=TransactionStatus.SUCCESS,
            tx_type=TransactionType.WALLET_FUND,
            external_reference=str(reference) if reference else None,
        )
        db.add(tx)
        db.flush()

        # Credit Wallet (credit_wallet atomically increments balance and creates a WalletLedger entry)
        wallet = get_or_create_wallet(db, user.id)
        desc = f"Wallet funding from {sender_name}" if sender_name else "Wallet funded via Aspfiy Transfer"
        credit_wallet(db, wallet, amount, reference, desc, sender_name=sender_name)
        db.commit()
        db.refresh(tx)

    _maybe_reward_first_deposit(db, user=user, reference=reference, amount=amount, status=TransactionStatus.SUCCESS)

    if getattr(user, "fcm_token", None):
        try:
            PushNotificationService.send_to_token(
                token=user.fcm_token,
                title="Wallet Funded",
                body=f"Your wallet has been credited with ₦{amount:,.2f} via Bank Transfer.",
                sound_type="balance_success",
            )
        except Exception as push_err:
            logger.warning("Failed to send push notification for Aspfiy webhook: %s", push_err)

    dispatch_developer_webhook(tx, user)

    return {"status": "success", "message": "wallet funded successfully"}
