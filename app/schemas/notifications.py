from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class BroadcastAnnouncementOut(BaseModel):
    id: int
    title: str
    message: str
    level: str
    is_active: bool
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    created_by_email: Optional[str] = None

    class Config:
        orm_mode = True


class BroadcastAnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=120)
    message: str = Field(..., min_length=6, max_length=2000)
    level: str = Field(default="info", min_length=4, max_length=16)
    is_active: bool = True
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class BroadcastAnnouncementUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=120)
    message: Optional[str] = Field(default=None, min_length=6, max_length=2000)
    level: Optional[str] = Field(default=None, min_length=4, max_length=16)
    is_active: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
