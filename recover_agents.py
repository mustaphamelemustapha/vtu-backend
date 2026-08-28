from app.core.database import SessionLocal
from app.models.user import User, UserRole
from app.models.agent import AgentStat

def recover_agents():
    db = SessionLocal()
    try:
        # Find all users who are currently 'customer' but have an AgentStat record
        agent_stats = db.query(AgentStat).all()
        recovered_count = 0
        
        for stat in agent_stats:
            user = db.query(User).filter(User.id == stat.agent_id).first()
            if user and user.role == UserRole.CUSTOMER:
                user.role = UserRole.AGENT
                recovered_count += 1
                
        if recovered_count > 0:
            db.commit()
            print(f"Successfully recovered {recovered_count} agents!")
        else:
            print("No agents needed recovery.")
            
    finally:
        db.close()

if __name__ == "__main__":
    recover_agents()
