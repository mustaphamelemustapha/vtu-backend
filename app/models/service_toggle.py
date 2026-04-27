from sqlalchemy import Column, Integer, String, Boolean
from app.core.database import Base
from app.models.base import TimestampMixin

class ServiceToggle(Base, TimestampMixin):
    __tablename__ = "service_toggles"

    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String(50), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
