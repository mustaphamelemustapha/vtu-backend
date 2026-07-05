from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.core.database import get_db
from app.dependencies import get_current_user
from app.models import User, Transaction, TransactionStatus
from app.schemas.leaderboard import LeaderboardResponse, LeaderboardUser

router = APIRouter()

@router.get("/", response_model=LeaderboardResponse)
def get_leaderboard(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Get top 50 users based on total successful transaction amount
    top_users_query = (
        db.query(
            User.id,
            User.full_name,
            User.email,
            User.profile_image_url,
            func.sum(Transaction.amount).label("score")
        )
        .join(Transaction, User.id == Transaction.user_id)
        .filter(Transaction.status == TransactionStatus.SUCCESS)
        .filter(User.role == "user") # Optionally exclude admins, but strings vs enums can be tricky. Let's just exclude admins if role exists.
        .group_by(User.id)
        .order_by(desc("score"))
        .limit(50)
        .all()
    )

    top_users = []
    current_user_data = None

    for rank, record in enumerate(top_users_query, start=1):
        username = record.full_name if record.full_name else record.email.split('@')[0]
        lb_user = LeaderboardUser(
            id=record.id,
            username=username,
            profile_image_url=record.profile_image_url,
            rank=rank,
            score=float(record.score or 0)
        )
        top_users.append(lb_user)
        if record.id == user.id:
            current_user_data = lb_user

    # If current user is not in top 50, fetch their rank
    if not current_user_data:
        # Calculate rank of current user
        user_score_query = (
            db.query(func.sum(Transaction.amount))
            .filter(Transaction.user_id == user.id)
            .filter(Transaction.status == TransactionStatus.SUCCESS)
            .scalar()
        )
        user_score = float(user_score_query or 0)

        # Count how many users have a higher score
        higher_scores_count = (
            db.query(func.count(User.id))
            .join(Transaction, User.id == Transaction.user_id)
            .filter(Transaction.status == TransactionStatus.SUCCESS)
            .group_by(User.id)
            .having(func.sum(Transaction.amount) > user_score)
            .count() # This won't work perfectly with group_by and having in SQLAlchemy without subquery.
        )
        
        # Let's do a subquery instead
        subq = (
            db.query(
                Transaction.user_id,
                func.sum(Transaction.amount).label("total_amount")
            )
            .filter(Transaction.status == TransactionStatus.SUCCESS)
            .group_by(Transaction.user_id)
            .subquery()
        )
        
        higher_scores_count = (
            db.query(func.count(subq.c.user_id))
            .filter(subq.c.total_amount > user_score)
            .scalar()
        )
        
        user_rank = (higher_scores_count or 0) + 1
        username = user.full_name if user.full_name else user.email.split('@')[0]
        
        current_user_data = LeaderboardUser(
            id=user.id,
            username=username,
            profile_image_url=user.profile_image_url,
            rank=user_rank,
            score=user_score
        )

    return LeaderboardResponse(
        top_users=top_users,
        current_user=current_user_data
    )
