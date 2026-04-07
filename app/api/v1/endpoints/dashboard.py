from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.endpoints.notifications import list_active_broadcasts
from app.api.v1.endpoints.transactions import list_transactions
from app.api.v1.endpoints.wallet import get_bank_transfer_accounts, get_wallet
from app.core.config import get_settings
from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.dashboard import DashboardSummaryOut

router = APIRouter()
settings = get_settings()


@router.get("/summary", response_model=DashboardSummaryOut)
def get_dashboard_summary(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    partial_failures: list[str] = []
    wallet = None
    transactions = []
    announcements = []
    bank_transfer_accounts = None

    try:
        wallet = get_wallet(user=user, db=db)
    except Exception:
        partial_failures.append("wallet")

    try:
        transactions = list_transactions(user=user, db=db)
    except Exception:
        partial_failures.append("transactions")

    try:
        announcements = list_active_broadcasts(user=user, db=db)
    except Exception:
        partial_failures.append("announcements")

    try:
        bank_transfer_accounts = get_bank_transfer_accounts(user=user, db=db)
    except HTTPException as exc:
        partial_failures.append("bank_transfer_accounts")
        bank_transfer_accounts = {
            "provider": settings.bank_transfer_provider.lower(),
            "account_reference": f"AXISVTU_{user.id}",
            "accounts": [],
            "requires_kyc": True,
            "requires_phone": False,
            "message": str(exc.detail or "Unable to fetch bank transfer accounts right now."),
        }
    except Exception:
        partial_failures.append("bank_transfer_accounts")
        bank_transfer_accounts = {
            "provider": settings.bank_transfer_provider.lower(),
            "account_reference": f"AXISVTU_{user.id}",
            "accounts": [],
            "requires_kyc": True,
            "requires_phone": False,
            "message": "Unable to fetch bank transfer accounts right now.",
        }

    return {
        "wallet": wallet,
        "transactions": transactions,
        "announcements": announcements,
        "bank_transfer_accounts": bank_transfer_accounts,
        "partial_failures": partial_failures,
    }
