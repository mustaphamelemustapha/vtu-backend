@router.post("/aspfiy")
async def aspfiy_webhook(request: Request, raw_body: bytes = Depends(get_raw_body), db: Session = Depends(get_db)):
    signature = (request.headers.get("x-wiaxy-signature") or request.headers.get("X-Wiaxy-Signature") or "").strip().lower()
    if not signature:
        logger.warning("Aspfiy webhook missing signature")
        raise HTTPException(status_code=401, detail="Missing signature")
        
    expected_signature = hashlib.md5(settings.aspfiy_secret_key.strip().encode()).hexdigest().lower()
    if signature != expected_signature:
        logger.warning("Aspfiy webhook invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(raw_body)
    event = str(payload.get("event") or "").strip().lower()
    
    # Aspfiy sends payment.success, PAYMENT_NOTIFIFICATION, PAYMENT_NOTIFICATION, etc.
    allowed_events = ("payment.success", "payment_notifification", "payment_notification", "payment.successful", "paid")
    if event and not any(allowed in event for allowed in allowed_events):
        logger.info("Aspfiy webhook: unhandled event '%s'", event)
        return {"status": "success", "message": f"unhandled event: {event}"}
        
    data = payload.get("data") or {}
    tx_type = str(data.get("type") or "").strip().upper()
    if tx_type and "TRANSFER" in tx_type and "RESERVED" not in tx_type:
        return {"status": "success", "message": "unhandled transaction type"}
        
    merchant_reference = (
        data.get("merchant_reference")
        or data.get("merchantReference")
        or payload.get("merchant_reference")
        or payload.get("merchantReference")
    )
    amount = Decimal(str(data.get("amount") or payload.get("amount") or 0))
    reference = (
        data.get("reference")
        or data.get("payment_reference")
        or data.get("aspfiy_ref")
        or payload.get("reference")
        or f"asp_{secrets.token_hex(6)}"
    )

    account_data = data.get("account") if isinstance(data.get("account"), dict) else {}
    customer_data = data.get("customer") if isinstance(data.get("customer"), dict) else {}

    acc_num = str(
        account_data.get("account_number")
        or data.get("account_number")
        or data.get("paid_into")
        or data.get("accountNumber")
        or ""
    ).strip()

    cust_email = str(
        customer_data.get("email")
        or data.get("email")
        or payload.get("email")
        or ""
    ).strip().lower()

    account = None
    # 1. Match by customer_reference
    if merchant_reference:
        account = db.query(VirtualAccount).filter(
            VirtualAccount.customer_reference == merchant_reference,
            VirtualAccount.provider == VirtualAccountProvider.ASPFIY
        ).first()

    # 2. Match by account_number
    if not account and acc_num:
        account = db.query(VirtualAccount).filter(
            VirtualAccount.account_number == acc_num,
            VirtualAccount.provider == VirtualAccountProvider.ASPFIY
        ).first()

    user = None
    if account:
        user = db.query(User).filter(User.id == account.user_id).first()
    
    # 3. Match from merchant_reference pattern (AXISVTU_<user_id>_aspfiy_...)
    if not user and merchant_reference and merchant_reference.startswith("AXISVTU_"):
        parts = merchant_reference.split("_")
        if len(parts) >= 2 and parts[1].isdigit():
            user = db.query(User).filter(User.id == int(parts[1])).first()

    # 4. Match from customer email in payload
    if not user and cust_email:
        user = db.query(User).filter(User.email == cust_email).first()

    if user and not account:
        logger.info("Aspfiy webhook: matched user %s; creating virtual account record", user.id)
        account = VirtualAccount(
            user_id=user.id,
            provider=VirtualAccountProvider.ASPFIY,
            account_number=acc_num or "paga",
            account_name=user.full_name or "Mele Data Customer",
            bank_name="Paga",
            bank_code="000",
            customer_reference=merchant_reference or f"AXISVTU_{user.id}_aspfiy_paga",
            reservation_reference=reference,
            status=VirtualAccountStatus.ACTIVE,
        )
        db.add(account)
        db.commit()

    if not user:
        logger.error("Aspfiy webhook: account/user not found for ref=%s acc=%s email=%s", merchant_reference, acc_num, cust_email)
        return {"status": "error", "message": "account not found"}
        
    # Idempotency check
    existing_tx = db.query(Transaction).filter(Transaction.reference == reference).first()
    if existing_tx:
        return {"status": "success", "message": "duplicate transaction"}

    # Credit Wallet
    wallet = get_or_create_wallet(db, user.id)
    new_balance = credit_wallet(db, user.id, amount)
    
    tx = Transaction(
        user_id=user.id,
        reference=reference,
        amount=amount,
        status=TransactionStatus.SUCCESS,
        tx_type=TransactionType.WALLET_FUND,
    )
    db.add(tx)
    
    ledger = WalletLedger(
        wallet_id=wallet.id,
        transaction_id=None,
        amount=amount,
        balance_after=new_balance,
        ledger_type=LedgerType.CREDIT,
        description=f"Wallet funded via Aspfiy Transfer",
    )
    db.add(ledger)
    db.commit()
    db.refresh(tx)
    
    ledger.transaction_id = tx.id
    db.commit()

    _maybe_reward_first_deposit(db, user=user, reference=reference, amount=amount, status=TransactionStatus.SUCCESS)

    PushNotificationService.send_transaction_notification(
        user_id=user.id,
        title="Wallet Funded",
        body=f"Your wallet has been credited with ₦{amount:,.2f} via Bank Transfer.",
    )

    dispatch_developer_webhook(db, user, "wallet.fund", {
        "reference": reference,
        "amount": str(amount),
        "status": "success",
        "method": "aspfiy_transfer",
        "channel": "bank_transfer"
    })

    return {"status": "success", "message": "wallet funded successfully"}
