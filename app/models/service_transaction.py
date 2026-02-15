from sqlalchemy import Column, Integer, String, ForeignKey, Numeric, Index, JSON
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.base import TimestampMixin


class ServiceTransaction(Base, TimestampMixin):
    """
    Stores non-data VTU purchases (airtime/cable/electricity/exam pins).

    We intentionally use plain strings for tx_type/status to avoid Postgres ENUM
    migrations on hosted environments where running alembic is not available.
    """

    __tablename__ = "service_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    reference = Column(String(64), unique=True, nullable=False, index=True)
    tx_type = Column(String(32), nullable=False)  # e.g. "airtime", "cable", "electricity", "exam"
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(24), nullable=False, default="pending")  # pending|success|failed|refunded

    provider = Column(String(64), nullable=True)  # e.g. mtn/dstv/ikeja
    customer = Column(String(128), nullable=True)  # e.g. phone, smartcard, meter
    product_code = Column(String(64), nullable=True)  # e.g. bouquet/code
    external_reference = Column(String(128), nullable=True)
    failure_reason = Column(String(255), nullable=True)

    meta = Column(JSON, nullable=True)  # service-specific fields (pin(s), meter_type, etc.)

    user = relationship("User", back_populates="service_transactions")


Index("ix_service_transactions_user_status", ServiceTransaction.user_id, ServiceTransaction.status)

