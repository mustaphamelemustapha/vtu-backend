import enum
from sqlalchemy import Column, Integer, String, Boolean, Enum, Index, DateTime
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin


class UserRole(str, enum.Enum):
    USER = "user"
    RESELLER = "reseller"
    ADMIN = "admin"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.USER)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    reset_token = Column(String(128), nullable=True, index=True)
    reset_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    verification_token = Column(String(128), nullable=True, index=True)
    verification_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    wallet = relationship("Wallet", back_populates="user", uselist=False)
    transactions = relationship("Transaction", back_populates="user")
    api_logs = relationship("ApiLog", back_populates="user")


Index("ix_users_role_active", User.role, User.is_active)
