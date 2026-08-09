import enum
from sqlalchemy import Column, Integer, String, Numeric, Enum, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin

class FinancialCategory(str, enum.Enum):
    OWNER_CAPITAL = "owner_capital"
    OWNER_WITHDRAWAL = "owner_withdrawal"
    BUSINESS_DEBT = "business_debt"
    RECEIVABLE = "receivable"
    INTERNAL_TRANSFER = "internal_transfer"
    MANUAL_ADJUSTMENT = "manual_adjustment"

class EntryType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"

class FinancialLedger(Base, TimestampMixin):
    __tablename__ = "financial_ledger"

    id = Column(Integer, primary_key=True, index=True)
    reference = Column(String(64), unique=True, nullable=False, index=True)
    source = Column(String(64), nullable=True) # e.g., "manual", "system"
    source_id = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="completed")
    category = Column(Enum(FinancialCategory), nullable=False, index=True)
    entry_type = Column(Enum(EntryType), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    party = Column(String(255), nullable=True) # E.g., "Adam", "Moniepoint"
    description = Column(String(255), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_by = relationship("User", foreign_keys=[created_by_id])
