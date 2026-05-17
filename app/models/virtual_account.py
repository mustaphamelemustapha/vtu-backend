import enum
from sqlalchemy import Column, Integer, String, ForeignKey, Enum
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.models.base import TimestampMixin

class VirtualAccountProvider(str, enum.Enum):
    MONNIFY = "monnify"
    PAYSTACK = "paystack"

class VirtualAccountStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class VirtualAccount(Base, TimestampMixin):
    __tablename__ = "virtual_accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(Enum(VirtualAccountProvider), nullable=False)
    account_number = Column(String(32), nullable=False)
    account_name = Column(String(255), nullable=False)
    bank_name = Column(String(255), nullable=False)
    bank_code = Column(String(16), nullable=False)
    customer_reference = Column(String(64), nullable=False, unique=True, index=True)
    reservation_reference = Column(String(64), nullable=False, unique=True)
    status = Column(Enum(VirtualAccountStatus), default=VirtualAccountStatus.ACTIVE, nullable=False)

    user = relationship("User", back_populates="virtual_accounts")
