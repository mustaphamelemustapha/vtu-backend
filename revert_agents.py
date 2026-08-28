from app.core.database import SessionLocal
from app.models.user import User, UserRole
from app.models.agent import AgentStat

def revert_agents():
    db = SessionLocal()
    try:
        # Find all current agents
        agents = db.query(User).filter(User.role == UserRole.AGENT).all()
        reverted_count = 0
        
        for user in agents:
            stat = db.query(AgentStat).filter(AgentStat.agent_id == user.id).first()
            
            # If they have sold less than 50GB (50 * 1024 = 51200 MB), they shouldn't be agents
            total_mb = stat.total_data_mb if stat else 0
            if total_mb < 51200:
                user.role = UserRole.CUSTOMER
                reverted_count += 1
                
        if reverted_count > 0:
            db.commit()
            print(f"Successfully reverted {reverted_count} users back to Customer!")
        else:
            print("No users needed reverting.")
            
    finally:
        db.close()

if __name__ == "__main__":
    revert_agents()
