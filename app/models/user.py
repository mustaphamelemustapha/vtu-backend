import enum
from sqlalchemy import Column, Integer, String, Boolean, Enum, Index, DateTime, ForeignKey
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
    phone_number = Column(String(32), nullable=True, index=True)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.USER)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    referral_code = Column(String(16), unique=True, nullable=False, index=True)
    referred_by_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    pin_hash = Column(String(255), nullable=True)
    pin_set_at = Column(DateTime(timezone=True), nullable=True)
    pin_failed_attempts = Column(Integer, default=0, nullable=False)
    pin_locked_until = Column(DateTime(timezone=True), nullable=True)
    pin_reset_token_hash = Column(String(255), nullable=True, index=True)
    pin_reset_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    reset_token = Column(String(128), nullable=True, index=True)
    reset_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    verification_token = Column(String(128), nullable=True, index=True)
    verification_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    fcm_token = Column(String(255), nullable=True)

    referred_by = relationship(
        "User",
        remote_side=[id],
        foreign_keys=[referred_by_id],
        backref="referred_users",
    )
    referrals_sent = relationship(
        "Referral",
        foreign_keys="Referral.referrer_id",
        back_populates="referrer",
    )
    referral_received = relationship(
        "Referral",
        foreign_keys="Referral.referred_user_id",
        back_populates="referred_user",
        uselist=False,
    )
    wallet = relationship("Wallet", back_populates="user", uselist=False)
    transactions = relationship("Transaction", back_populates="user")
    service_transactions = relationship("ServiceTransaction", back_populates="user")
    api_logs = relationship("ApiLog", back_populates="user")


Index("ix_users_role_active", User.role, User.is_active)
