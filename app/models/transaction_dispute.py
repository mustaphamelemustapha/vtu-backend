import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class DisputeStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class TransactionDispute(Base, TimestampMixin):
    __tablename__ = "transaction_disputes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    transaction_reference = Column(String(64), nullable=False, index=True)
    tx_type = Column(String(32), nullable=False)
    category = Column(String(32), nullable=False, default="delivery_issue")
    reason = Column(Text, nullable=False)
    status = Column(Enum(DisputeStatus), nullable=False, default=DisputeStatus.OPEN)
    admin_note = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(128), nullable=True)

    user = relationship("User")


Index(
    "ix_transaction_disputes_user_reference",
    TransactionDispute.user_id,
    TransactionDispute.transaction_reference,
)
Index(
    "ix_transaction_disputes_status_created",
    TransactionDispute.status,
    TransactionDispute.created_at,
)
