from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import inspect
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User, Transaction, ServiceTransaction, TransactionDispute, DisputeStatus
from app.schemas.transaction import TransactionOut, TransactionReportOut, TransactionReportRequest

router = APIRouter()


def _has_dispute_table(db: Session) -> bool:
    try:
        return inspect(db.bind).has_table("transaction_disputes")
    except Exception:
        return False


def _ensure_dispute_table(db: Session) -> bool:
    if _has_dispute_table(db):
        return True
    try:
        TransactionDispute.__table__.create(bind=db.bind, checkfirst=True)
        return True
    except Exception:
        return False


def _normalize_tx_type(value) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "")


def _find_user_tx_type(db: Session, *, user_id: int, reference: str) -> str | None:
    tx = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.reference == reference).first()
    if tx:
        return _normalize_tx_type(tx.tx_type)
    try:
        if inspect(db.bind).has_table("service_transactions"):
            extra = db.query(ServiceTransaction).filter(
                ServiceTransaction.user_id == user_id,
                ServiceTransaction.reference == reference,
            ).first()
            if extra:
                return _normalize_tx_type(extra.tx_type)
    except Exception:
        return None
    return None


@router.get("/me", response_model=list[TransactionOut])

def list_transactions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    base = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    extra = []
    try:
        if inspect(db.bind).has_table("service_transactions"):
            extra = db.query(ServiceTransaction).filter(ServiceTransaction.user_id == user.id).all()
    except Exception:
        # On hosted environments, new tables might not exist until a redeploy. Avoid breaking history.
        extra = []

    open_report_refs: set[str] = set()
    if _has_dispute_table(db):
        rows = (
            db.query(TransactionDispute.transaction_reference)
            .filter(
                TransactionDispute.user_id == user.id,
                TransactionDispute.status == DisputeStatus.OPEN,
            )
            .all()
        )
        open_report_refs = {str(row[0]) for row in rows if row and row[0]}

    items: list[dict] = []
    for tx in base:
        items.append(
            {
                "id": tx.id,
                "created_at": tx.created_at,
                "reference": tx.reference,
                "network": tx.network,
                "data_plan_code": tx.data_plan_code,
                "amount": tx.amount,
                "status": tx.status,
                "tx_type": tx.tx_type,
                "external_reference": tx.external_reference,
                "failure_reason": tx.failure_reason,
                "meta": None,
                "has_open_report": tx.reference in open_report_refs,
            }
        )
    for tx in extra:
        items.append(
            {
                "id": tx.id,
                "created_at": tx.created_at,
                "reference": tx.reference,
                "network": tx.provider,
                "data_plan_code": tx.product_code,
                "amount": tx.amount,
                "status": tx.status,
                "tx_type": tx.tx_type,
                "external_reference": tx.external_reference,
                "failure_reason": tx.failure_reason,
                "meta": tx.meta,
                "has_open_report": tx.reference in open_report_refs,
            }
        )

    items.sort(key=lambda r: (r.get("created_at") is not None, r.get("created_at")), reverse=True)
    return items


@router.get("/reports/me", response_model=list[TransactionReportOut])
def list_my_reports(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not _has_dispute_table(db):
        return []
    rows = (
        db.query(TransactionDispute)
        .filter(TransactionDispute.user_id == user.id)
        .order_by(TransactionDispute.created_at.desc(), TransactionDispute.id.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "created_at": row.created_at,
            "transaction_reference": row.transaction_reference,
            "tx_type": row.tx_type,
            "category": row.category,
            "reason": row.reason,
            "status": _normalize_tx_type(row.status),
            "admin_note": row.admin_note,
            "resolved_at": row.resolved_at,
        }
        for row in rows
    ]


@router.post("/{reference}/report", response_model=TransactionReportOut)
def report_transaction(
    reference: str,
    payload: TransactionReportRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tx_type = _find_user_tx_type(db, user_id=user.id, reference=reference)
    if not tx_type:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if not _ensure_dispute_table(db):
        raise HTTPException(status_code=503, detail="Issue reporting is temporarily unavailable")

    category = (payload.category or "delivery_issue").strip().lower()
    if category not in {"delivery_issue", "wrong_recipient", "duplicate_charge", "other"}:
        category = "other"

    existing_open = (
        db.query(TransactionDispute)
        .filter(
            TransactionDispute.user_id == user.id,
            TransactionDispute.transaction_reference == reference,
            TransactionDispute.status == DisputeStatus.OPEN,
        )
        .order_by(TransactionDispute.id.desc())
        .first()
    )
    if existing_open:
        return {
            "id": existing_open.id,
            "created_at": existing_open.created_at,
            "transaction_reference": existing_open.transaction_reference,
            "tx_type": existing_open.tx_type,
            "category": existing_open.category,
            "reason": existing_open.reason,
            "status": _normalize_tx_type(existing_open.status),
            "admin_note": existing_open.admin_note,
            "resolved_at": existing_open.resolved_at,
        }

    row = TransactionDispute(
        user_id=user.id,
        transaction_reference=reference,
        tx_type=tx_type,
        category=category,
        reason=payload.reason.strip(),
        status=DisputeStatus.OPEN,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "created_at": row.created_at,
        "transaction_reference": row.transaction_reference,
        "tx_type": row.tx_type,
        "category": row.category,
        "reason": row.reason,
        "status": _normalize_tx_type(row.status),
        "admin_note": row.admin_note,
        "resolved_at": row.resolved_at,
    }
