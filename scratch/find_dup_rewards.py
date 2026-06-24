import os
import sys

# Add backend root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.agent import AgentReward
from collections import defaultdict

db = SessionLocal()
try:
    rewards = db.query(AgentReward).all()
    print(f"Total reward entries: {len(rewards)}")
    
    seen = defaultdict(list)
    for r in rewards:
        seen[(r.agent_id, r.campaign_id)].append(r)
        
    duplicates = {k: v for k, v in seen.items() if len(v) > 1}
    
    if not duplicates:
        print("No duplicate agent reward entries found in local DB.")
    else:
        print(f"Found {len(duplicates)} duplicate sets:")
        for (agent_id, campaign_id), list_rewards in duplicates.items():
            print(f"Agent ID: {agent_id}, Campaign ID: {campaign_id}")
            for r in list_rewards:
                print(f"  Reward ID: {r.id}, Amount: {r.amount}, Status: {r.status}, Created: {r.created_at}, Ref: {r.transaction_reference}")
finally:
    db.close()
