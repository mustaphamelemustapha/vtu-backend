import enum

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text

from app.core.database import Base, SafeSQLEnum
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
    level = Column(
        SafeSQLEnum(
            AnnouncementLevel,
            name="announcementlevel",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=AnnouncementLevel.INFO,
    )
    is_active = Column(Boolean, nullable=False, default=True)
    starts_at = Column(DateTime(timezone=True), nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    created_by_email = Column(String(255), nullable=True)
    button_label = Column(String(50), nullable=True)
    button_link = Column(String(255), nullable=True)
    image_url = Column(String(500), nullable=True)
    is_popup = Column(Boolean, nullable=False, default=False)
    brand = Column(String(32), nullable=True, default="all", index=True)


Index(
    "ix_broadcast_announcements_active_window",
    BroadcastAnnouncement.is_active,
    BroadcastAnnouncement.starts_at,
    BroadcastAnnouncement.ends_at,
)
