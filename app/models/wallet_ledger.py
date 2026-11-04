import enum
from sqlalchemy import Column, Integer, ForeignKey, Numeric, String, Index
from sqlalchemy.orm import relationship
from app.core.database import Base, SafeSQLEnum
from app.models.base import TimestampMixin


class LedgerType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value.lower() == val_lower or member.name.lower() == val_lower:
                    return member
        return cls.CREDIT


class WalletLedger(Base, TimestampMixin):
    __tablename__ = "wallet_ledger"

    id = Column(Integer, primary_key=True, index=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    entry_type = Column(
        SafeSQLEnum(
            LedgerType,
            name="ledgertype",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    reference = Column(String(64), nullable=False, index=True)
    description = Column(String(255), nullable=False)

    wallet = relationship("Wallet", back_populates="ledger_entries")


Index("ix_wallet_ledger_wallet_id_type", WalletLedger.wallet_id, WalletLedger.entry_type)
