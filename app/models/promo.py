from sqlalchemy import Column, Integer, String, Boolean, Numeric, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin

class PromoCode(Base, TimestampMixin):
    __tablename__ = "promo_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    description = Column(String(255), nullable=True)
    discount_amount = Column(Numeric(12, 2), nullable=False) # The amount or percentage
    is_percentage = Column(Boolean, default=False, nullable=False) # True if it's a percentage
    max_uses_per_user = Column(Integer, default=1, nullable=False)
    max_total_uses = Column(Integer, nullable=True)
    current_uses = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    
    # New targeting fields
    applicable_network = Column(String(20), default="ALL", nullable=False)
    applicable_plan_size = Column(String(20), default="ALL", nullable=False)
    target_audience = Column(String(20), default="ALL_USERS", nullable=False)
    
    user_promos = relationship("UserPromo", back_populates="promo_code")

class UserPromo(Base, TimestampMixin):
    __tablename__ = "user_promos"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    promo_code_id = Column(Integer, ForeignKey("promo_codes.id"), nullable=False, index=True)
    status = Column(String(20), default="READY", nullable=False) # READY, USED, EXPIRED
    used_at = Column(DateTime(timezone=True), nullable=True)
    
    user = relationship("User", back_populates="promos")
    promo_code = relationship("PromoCode", back_populates="user_promos")

Index("ix_user_promos_user_status", UserPromo.user_id, UserPromo.status)
