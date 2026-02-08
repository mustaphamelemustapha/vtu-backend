from sqlalchemy import Column, Integer, String, ForeignKey, Numeric, Index
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin


class ApiLog(Base, TimestampMixin):
    __tablename__ = "api_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    service = Column(String(64), nullable=False)
    endpoint = Column(String(255), nullable=False)
    status_code = Column(Integer, nullable=False)
    duration_ms = Column(Numeric(10, 2), nullable=False)
    reference = Column(String(64), nullable=True)
    success = Column(Integer, nullable=False)

    user = relationship("User", back_populates="api_logs")


Index("ix_api_logs_service_status", ApiLog.service, ApiLog.status_code)
