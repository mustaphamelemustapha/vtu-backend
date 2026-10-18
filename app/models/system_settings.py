from sqlalchemy import Column, Integer, String, Text, Boolean
from app.core.database import Base

class SystemSettings(Base):
    __tablename__ = "system_settings_config"

    id = Column(Integer, primary_key=True, index=True)
    active_vtu_provider = Column(String(50), default="autosync")
    vtu_api_key = Column(String(255), nullable=True)
    vtu_secret_key = Column(String(255), nullable=True)
    active_payment_gateway = Column(String(50), default="monnify")
    monnify_api_key = Column(String(255), nullable=True)
    monnify_secret_key = Column(String(255), nullable=True)
    monnify_contract_code = Column(String(255), nullable=True)
    paystack_secret_key = Column(String(255), nullable=True)
    app_name = Column(String(100), default="Mele Data")
    support_phone = Column(String(50), nullable=True)
    support_whatsapp = Column(String(50), nullable=True)
    announcement_banner = Column(Text, nullable=True)
    maintenance_mode = Column(Boolean, default=False)
    maintenance_message = Column(Text, nullable=True, default="Mele Data is currently undergoing scheduled server upgrades. Services will resume shortly.")
    min_app_version = Column(String(50), nullable=True, default="1.0.0")
    latest_app_version = Column(String(50), nullable=True, default="1.0.0")
    play_store_url = Column(String(255), nullable=True, default="https://play.google.com/store/apps/details?id=com.mmtech.axisvtu")
    app_store_url = Column(String(255), nullable=True, default="https://apps.apple.com/app/meledata")
    release_notes = Column(Text, nullable=True)

