@router.post("/aspfiy")
async def aspfiy_webhook(request: Request, raw_body: bytes = Depends(get_raw_body), db: Session = Depends(get_db)):
    signature = request.headers.get("x-wiaxy-signature")
    if not signature:
        logger.warning("Aspfiy webhook missing signature")
        raise HTTPException(status_code=401, detail="Missing signature")
        
    expected_signature = hashlib.md5(settings.aspfiy_secret_key.encode()).hexdigest()
    if signature != expected_signature:
        logger.warning("Aspfiy webhook invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(raw_body)
    event = payload.get("event")
    
    # Based on docs: event == PAYMENT_NOTIFIFICATION (spelled like that in docs)
    # or PAYMENT_NOTIFICATION
    if event not in ("PAYMENT_NOTIFIFICATION", "PAYMENT_NOTIFICATION"):
        return {"status": "success", "message": "unhandled event"}
        
    data = payload.get("data") or {}
    tx_type = data.get("type")
    if tx_type != "RESERVED_ACCOUNT_TRANSACTION":
        return {"status": "success", "message": "unhandled transaction type"}
        
    merchant_reference = data.get("merchant_reference")
    amount = Decimal(str(data.get("amount", 0)))
    reference = data.get("reference") or f"asp_{secrets.token_hex(6)}"
    
    if not merchant_reference:
        return {"status": "error", "message": "missing merchant_reference"}
        
    # The merchant_reference is our customer_reference (e.g., AXISVTU_<user_id>_aspfiy_...)
    account = db.query(VirtualAccount).filter(
        VirtualAccount.customer_reference == merchant_reference,
        VirtualAccount.provider == VirtualAccountProvider.ASPFIY
    ).first()
    
    if not account:
        logger.error(f"Aspfiy webhook: account not found for reference {merchant_reference}")
        return {"status": "error", "message": "account not found"}
        
    user = db.query(User).filter(User.id == account.user_id).first()
    if not user:
        return {"status": "error", "message": "user not found"}
        
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
