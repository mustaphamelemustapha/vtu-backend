from sqlalchemy import Column, Integer, String, JSON
from app.core.database import Base
from app.models.base import TimestampMixin

class AdminAuditLog(Base, TimestampMixin):
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_email = Column(String(255), index=True, nullable=False)
    action = Column(String(100), nullable=False)
    target = Column(String(100), nullable=True)
    details = Column(JSON, nullable=True)
