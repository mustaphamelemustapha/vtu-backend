from sqlalchemy import Column, Integer, ForeignKey, Numeric, Boolean, Index
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin


class Wallet(Base, TimestampMixin):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    balance = Column(Numeric(12, 2), default=0, nullable=False)
    is_locked = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="wallet")
    ledger_entries = relationship("WalletLedger", back_populates="wallet")


Index("ix_wallets_user_id", Wallet.user_id)
