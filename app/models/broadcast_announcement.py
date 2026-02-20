import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, Index, Integer, String, Text

from app.core.database import Base
from app.models.base import TimestampMixin


class AnnouncementLevel(str, enum.Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    CRITICAL = "critical"


class BroadcastAnnouncement(Base, TimestampMixin):
    __tablename__ = "broadcast_announcements"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(120), nullable=False)
    message = Column(Text, nullable=False)
    level = Column(Enum(AnnouncementLevel), nullable=False, default=AnnouncementLevel.INFO)
    is_active = Column(Boolean, nullable=False, default=True)
    starts_at = Column(DateTime(timezone=True), nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    created_by_email = Column(String(255), nullable=True)


Index(
    "ix_broadcast_announcements_active_window",
    BroadcastAnnouncement.is_active,
    BroadcastAnnouncement.starts_at,
    BroadcastAnnouncement.ends_at,
)
