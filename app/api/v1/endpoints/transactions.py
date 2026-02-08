from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User, Transaction
from app.schemas.transaction import TransactionOut

router = APIRouter()


@router.get("/me", response_model=list[TransactionOut])

def list_transactions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    txs = db.query(Transaction).filter(Transaction.user_id == user.id).order_by(Transaction.id.desc()).all()
    return txs
