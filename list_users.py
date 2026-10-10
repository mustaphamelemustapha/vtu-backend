from app.core.database import SessionLocal
from app.models.user import User

db = SessionLocal()
try:
    users = db.query(User).all()
    for u in users:
        print(f"{u.email} - {u.role}")
finally:
    db.close()
