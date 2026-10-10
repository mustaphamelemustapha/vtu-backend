from sqlalchemy import Column, String, Boolean, Float, JSON, Enum as SQLEnum, DateTime
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID
import uuid
import enum
from app.core.database import Base

class ProviderType(str, enum.Enum):
    AIRTIME = "AIRTIME"
    DATA = "DATA"
    CABLE = "CABLE"
    ELECTRICITY = "ELECTRICITY"
    GENERAL = "GENERAL"

class IntegrationProvider(Base):
    """
    Model for VTU/Data/Cable Providers (e.g. SMEPlug, ClubKonnect, Husmodata)
    """
    __tablename__ = "integration_providers"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    identifier = Column(String(50), unique=True, nullable=False, index=True) # e.g. "smeplug"
    
    base_url = Column(String(255), nullable=True)
    api_key = Column(String(512), nullable=True)
    api_secret = Column(String(512), nullable=True)
    additional_config = Column(JSON, nullable=True, default={}) 
    
    is_active = Column(Boolean, default=False)
    balance = Column(Float, default=0.0)
    
    # Which services does this provider actively handle?
    # e.g. ["airtime", "data"]
    supported_services = Column(JSON, nullable=True, default=[])
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PaymentGateway(Base):
    """
    Model for Payment Gateways (e.g. Asfiy, Monnify, Paystack, Flutterwave)
    """
    __tablename__ = "payment_gateways"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    identifier = Column(String(50), unique=True, nullable=False, index=True) # e.g. "asfiy"
    
    public_key = Column(String(512), nullable=True)
    secret_key = Column(String(512), nullable=True)
    contract_code = Column(String(255), nullable=True)
    
    # Expected webhook URL format to display to the admin
    webhook_url = Column(String(255), nullable=True) 
    
    # Used for toggling sub-channels like Palmpay, Paga, Safehaven
    additional_config = Column(JSON, nullable=True, default={}) 
    
    charge_percentage = Column(Float, default=0.0)
    charge_flat = Column(Float, default=0.0)
    
    is_active = Column(Boolean, default=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
