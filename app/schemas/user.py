from pydantic import BaseModel, EmailStr
from datetime import datetime
from app.models.user import UserRole


class UserOut(BaseModel):
    id: int
    email: EmailStr
    phone_number: str | None = None
    full_name: str
    profile_image_url: str | None = None
    role: UserRole
    is_active: bool
    is_verified: bool
    referral_code: str | None = None
    state: str | None = None
    agent_upgrade_seen: bool = False
    is_developer: bool = False
    developer_status: str | None = 'none'
    api_public_key: str | None = None
    has_api_keys: bool = False
    has_bvn: bool = False
    has_nin: bool = False
    kyc_verified: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        orm_mode = True
