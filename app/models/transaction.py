import enum
from sqlalchemy import Column, Integer, String, ForeignKey, Numeric, Enum, Index
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    REFUNDED = "refunded"


class TransactionType(str, enum.Enum):
    DATA = "data"
    WALLET_FUND = "wallet_fund"


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reference = Column(String(64), unique=True, nullable=False, index=True)
    network = Column(String(32), nullable=True)
    data_plan_code = Column(String(64), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(Enum(TransactionStatus), nullable=False, default=TransactionStatus.PENDING)
    tx_type = Column(Enum(TransactionType), nullable=False)
    external_reference = Column(String(64), nullable=True)
    failure_reason = Column(String(255), nullable=True)

    user = relationship("User", back_populates="transactions")


Index("ix_transactions_user_status", Transaction.user_id, Transaction.status)
Index("ix_transactions_type_status", Transaction.tx_type, Transaction.status)
