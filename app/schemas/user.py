from pydantic import BaseModel, EmailStr
from datetime import datetime
from app.models.user import UserRole


class UserOut(BaseModel):
    id: int
    email: EmailStr
    phone_number: str | None = None
    full_name: str
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        orm_mode = True
