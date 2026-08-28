from app.core.database import SessionLocal
from app.models.user import User, UserRole
from app.models.agent import AgentStat
from sqlalchemy import func

def print_stats():
    db = SessionLocal()
    try:
        print("\n--- Current Roles in DB ---")
        counts = db.query(User.role, func.count(User.id)).group_by(User.role).all()
        for role, count in counts:
            print(f"{role}: {count} users")
            
        print("\n--- Users over 50GB ---")
        over_50 = db.query(AgentStat).filter(AgentStat.total_data_mb >= 51200).count()
        print(f"Total users who sold >= 50GB: {over_50}")
    finally:
        db.close()

if __name__ == "__main__":
    print_stats()
