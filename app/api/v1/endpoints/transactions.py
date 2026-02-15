from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User, Transaction, ServiceTransaction
from app.schemas.transaction import TransactionOut

router = APIRouter()


@router.get("/me", response_model=list[TransactionOut])

def list_transactions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    base = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    extra = db.query(ServiceTransaction).filter(ServiceTransaction.user_id == user.id).all()

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
            }
        )

    items.sort(key=lambda r: (r.get("created_at") is not None, r.get("created_at")), reverse=True)
    return items
