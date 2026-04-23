from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.referrals import ReferralDashboardOut
from app.services.referrals import get_referral_dashboard

router = APIRouter()


@router.get('/me', response_model=ReferralDashboardOut)
def get_my_referrals(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_referral_dashboard(db, user=user)
