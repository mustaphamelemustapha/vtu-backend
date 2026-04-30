from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import DataPlan, Transaction, TransactionStatus, TransactionType
from app.services.amigo import AmigoApiError, AmigoClient, normalize_plan_code, resolve_network_id
from app.services.wallet import credit_wallet, get_or_create_wallet

logger = logging.getLogger(__name__)
settings = get_settings()

_SUCCESS_STATUS = {"success", "successful", "delivered", "completed", "ok", "done"}
_PENDING_STATUS = {"pending", "processing", "queued", "in_progress", "accepted", "submitted"}
_FAILURE_STATUS = {"failed", "fail", "error", "rejected", "declined", "cancelled", "canceled", "refunded"}

_AMBIGUOUS_HINTS = (
    "non-json",
    "invalid json",
    "unexpected response",
    "temporarily unavailable",
    "remote protocol",
    "timeout",
    "timed out",
)
_RECHECK_TAG = "[rechecked-once]"
_DEFINITIVE_FAILURE_CODES = {
    "invalid_token",
    "plan_not_found",
    "insufficient_balance",
    "invalid_network",
    "invalid_phone",
    "coming_soon",
}
_DEFINITIVE_FAILURE_HINTS = (
    "invalid token",
    "plan not found",
    "insufficient balance",
    "invalid network",
    "invalid phone",
    "coming soon",
)

_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None


def _normalize_text(value: object) -> str:
    return str(value or "").strip().lower()


def _classify_outcome(response: dict) -> str:
    status_text = _normalize_text(response.get("status") or response.get("delivery_status") or response.get("state"))
    message_text = _normalize_text(response.get("message") or response.get("detail") or response.get("remark"))
    success_flag = response.get("success")
    if isinstance(success_flag, bool) and success_flag:
        return TransactionStatus.SUCCESS.value
    if status_text in _SUCCESS_STATUS:
        return TransactionStatus.SUCCESS.value
    if isinstance(success_flag, bool) and not success_flag and status_text in _FAILURE_STATUS:
        return TransactionStatus.FAILED.value
    if status_text in _FAILURE_STATUS:
        return TransactionStatus.FAILED.value
    if status_text in _PENDING_STATUS:
        return TransactionStatus.PENDING.value
    if "success" in message_text or "delivered" in message_text:
        return TransactionStatus.SUCCESS.value
    if "failed" in message_text or "declined" in message_text or "rejected" in message_text:
        return TransactionStatus.FAILED.value
    return TransactionStatus.PENDING.value


def _is_ambiguous_reason(reason: str | None) -> bool:
    text = _normalize_text(reason)
    return any(hint in text for hint in _AMBIGUOUS_HINTS)


def _should_attempt_recheck(tx: Transaction) -> bool:
    if tx.status != TransactionStatus.PENDING or tx.tx_type != TransactionType.DATA:
        return False
    return True


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _is_definitive_failure(response: dict | None = None, reason: str | None = None, status_code: int | None = None) -> bool:
    payload = response or {}
    code = _normalize_text(payload.get("error") or payload.get("code") or "")
    message = _normalize_text(reason or payload.get("message") or payload.get("detail") or payload.get("remark"))
    if code in _DEFINITIVE_FAILURE_CODES:
        return True
    if _contains_any(message, _DEFINITIVE_FAILURE_HINTS):
        return True
    if status_code is not None and int(status_code) in {401, 403, 404, 422}:
        return True
    return False


def _finalize_refund(db: Session, tx: Transaction, reason: str) -> None:
    wallet = get_or_create_wallet(db, tx.user_id)
    tx.status = TransactionStatus.FAILED
    tx.failure_reason = reason[:255]
    credit_wallet(db, wallet, Decimal(tx.amount), tx.reference, "Auto refund after pending reconciliation failure")
    tx.status = TransactionStatus.REFUNDED


def reconcile_pending_data_once(limit: int = 50) -> dict[str, int]:
    db = SessionLocal()
    processed = 0
    moved_success = 0
    moved_refunded = 0
    stayed_pending = 0

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(10, settings.pending_reconcile_min_age_seconds))
        rows = (
            db.query(Transaction)
            .filter(
                Transaction.tx_type == TransactionType.DATA,
                Transaction.status == TransactionStatus.PENDING,
                Transaction.created_at <= cutoff,
            )
            .order_by(Transaction.created_at.asc())
            .limit(max(1, limit))
            .all()
        )

        client = AmigoClient()
        for tx in rows:
            if not _should_attempt_recheck(tx):
                continue

            processed += 1
            plan = db.query(DataPlan).filter(DataPlan.plan_code == tx.data_plan_code).first()
            network = str(tx.network or (plan.network if plan else "")).strip()
            recipient_phone = str(tx.recipient_phone or "").strip()
            if not recipient_phone:
                tx.failure_reason = f"{str(tx.failure_reason or '').strip()} {_RECHECK_TAG}".strip()[:255]
                stayed_pending += 1
                db.commit()
                continue
            network_id = resolve_network_id(network, tx.data_plan_code)
            if network_id is None:
                _finalize_refund(db, tx, "Unsupported network during pending reconciliation")
                moved_refunded += 1
                db.commit()
                continue

            try:
                response = client.purchase_data(
                    {
                        "network": network_id,
                        "mobile_number": recipient_phone,
                        "plan": normalize_plan_code(tx.data_plan_code),
                        "Ported_number": True,
                    },
                    idempotency_key=tx.reference,
                )
                outcome = _classify_outcome(response)
                if outcome == TransactionStatus.SUCCESS.value:
                    tx.status = TransactionStatus.SUCCESS
                    tx.failure_reason = None
                    tx.external_reference = (
                        response.get("reference")
                        or response.get("transaction_reference")
                        or response.get("transaction_id")
                        or tx.external_reference
                    )
                    moved_success += 1
                elif outcome == TransactionStatus.FAILED.value:
                    msg = str(response.get("message") or "Provider rejected transaction")
                    if _is_definitive_failure(response=response, reason=msg):
                        _finalize_refund(db, tx, msg)
                        moved_refunded += 1
                    else:
                        stayed_pending += 1
                else:
                    tx.failure_reason = f"{str(tx.failure_reason or '').strip()} {_RECHECK_TAG}".strip()[:255]
                    stayed_pending += 1
                db.commit()
            except AmigoApiError as exc:
                msg = str(exc.message or "Provider reconciliation error").strip()
                code = exc.status_code or 0
                # Definitive 4xx failures can be safely refunded.
                if _is_definitive_failure(reason=msg, status_code=code):
                    _finalize_refund(db, tx, msg)
                    moved_refunded += 1
                else:
                    stayed_pending += 1
                db.commit()
            except Exception as exc:
                stayed_pending += 1
                db.commit()
                logger.warning("Pending reconcile exception for %s: %s", tx.reference, exc)
    finally:
        db.close()

    return {
        "processed": processed,
        "success": moved_success,
        "refunded": moved_refunded,
        "pending": stayed_pending,
    }


def _reconcile_loop() -> None:
    logger.info(
        "Pending data reconciliation worker started (interval=%ss, max_batch=%s).",
        settings.pending_reconcile_interval_seconds,
        settings.pending_reconcile_batch_size,
    )
    while not _stop_event.is_set():
        try:
            stats = reconcile_pending_data_once(limit=settings.pending_reconcile_batch_size)
            if stats["processed"] > 0:
                logger.info("Pending reconcile stats: %s", stats)
        except Exception as exc:
            logger.warning("Pending reconciliation loop failed: %s", exc)
        _stop_event.wait(timeout=max(20, settings.pending_reconcile_interval_seconds))
    logger.info("Pending data reconciliation worker stopped.")


def start_pending_reconcile_worker() -> None:
    global _worker_thread
    if not settings.pending_reconcile_enabled:
        logger.info("Pending reconcile worker disabled by config.")
        return
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_reconcile_loop, name="pending-reconcile-worker", daemon=True)
    _worker_thread.start()


def stop_pending_reconcile_worker() -> None:
    _stop_event.set()
