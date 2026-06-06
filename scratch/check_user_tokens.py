import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import SessionLocal
from app.models import User

def check_tokens():
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.fcm_token != None, User.fcm_token != "").all()
        print(f"Found {len(users)} users with active FCM tokens:")
        for u in users:
            print(f"ID: {u.id}, Email: {u.email}, Token: {u.fcm_token[:30]}...")
    finally:
        db.close()

if __name__ == "__main__":
    check_tokens()
