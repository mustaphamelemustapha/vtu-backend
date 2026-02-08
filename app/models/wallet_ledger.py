import enum
from sqlalchemy import Column, Integer, ForeignKey, Numeric, String, Enum, Index
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin


class LedgerType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class WalletLedger(Base, TimestampMixin):
    __tablename__ = "wallet_ledger"

    id = Column(Integer, primary_key=True, index=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    entry_type = Column(Enum(LedgerType), nullable=False)
    reference = Column(String(64), nullable=False, index=True)
    description = Column(String(255), nullable=False)

    wallet = relationship("Wallet", back_populates="ledger_entries")


Index("ix_wallet_ledger_wallet_id_type", WalletLedger.wallet_id, WalletLedger.entry_type)
