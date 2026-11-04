import enum
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base, SafeSQLEnum
from app.models.base import TimestampMixin

class VirtualAccountProvider(str, enum.Enum):
    MONNIFY = "monnify"
    PAYSTACK = "paystack"
    BILLSTACK = "billstack"
    ASPFIY = "aspfiy"
    ASPIFY = "aspify"
    SQUAD = "squad"
    SAFEHAVEN = "safehaven"
    PALMPAY = "palmpay"
    MONIEPOINT = "moniepoint"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value.lower() == val_lower or member.name.lower() == val_lower:
                    return member
        return cls.MONNIFY

class VirtualAccountStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    PENDING = "pending"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value.lower() == val_lower or member.name.lower() == val_lower:
                    return member
        return cls.ACTIVE

class VirtualAccount(Base, TimestampMixin):
    __tablename__ = "virtual_accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(SafeSQLEnum(VirtualAccountProvider, name="virtualaccountprovider", values_callable=lambda obj: [e.value for e in obj]), nullable=False)
    account_number = Column(String(32), nullable=False)
    account_name = Column(String(255), nullable=False)
    bank_name = Column(String(255), nullable=False)
    bank_code = Column(String(16), nullable=False)
    customer_reference = Column(String(64), nullable=False, unique=True, index=True)
    reservation_reference = Column(String(64), nullable=False, unique=True)
    status = Column(SafeSQLEnum(VirtualAccountStatus, name="virtualaccountstatus", values_callable=lambda obj: [e.value for e in obj]), default=VirtualAccountStatus.ACTIVE, nullable=False)

    user = relationship("User", back_populates="virtual_accounts")
