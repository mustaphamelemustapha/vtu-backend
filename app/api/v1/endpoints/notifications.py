from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, inspect, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models import (
    AnnouncementLevel,
    BroadcastAnnouncement,
    User,
)
from app.schemas.notifications import (
    BroadcastAnnouncementCreate,
    BroadcastAnnouncementOut,
    BroadcastAnnouncementUpdate,
)

router = APIRouter()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _has_table(db: Session) -> bool:
    try:
        return inspect(db.get_bind()).has_table("broadcast_announcements")
    except Exception:
        return False


def _ensure_table(db: Session) -> bool:
    if _has_table(db):
        return True
    try:
        BroadcastAnnouncement.__table__.create(bind=db.get_bind(), checkfirst=True)
        return True
    except Exception:
        return False


def _coerce_level(raw: str | None) -> AnnouncementLevel:
    value = str(raw or "info").strip().lower()
    for level in AnnouncementLevel:
        if value == level.value:
            return level
    raise HTTPException(status_code=400, detail="Invalid announcement level")


def _validate_window(starts_at: datetime | None, ends_at: datetime | None) -> None:
    if starts_at and ends_at and ends_at <= starts_at:
        raise HTTPException(status_code=400, detail="ends_at must be after starts_at")


def _to_out(item: BroadcastAnnouncement) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "message": item.message,
        "level": item.level.value if hasattr(item.level, "value") else str(item.level),
        "is_active": bool(item.is_active),
        "starts_at": item.starts_at,
        "ends_at": item.ends_at,
        "created_at": item.created_at,
        "created_by_email": item.created_by_email,
    }


@router.get("/broadcast", response_model=list[BroadcastAnnouncementOut])
def list_active_broadcasts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ = user
    if not _has_table(db):
        return []
    now = _utcnow()
    rows = (
        db.query(BroadcastAnnouncement)
        .filter(
            and_(
                BroadcastAnnouncement.is_active.is_(True),
                or_(BroadcastAnnouncement.starts_at.is_(None), BroadcastAnnouncement.starts_at <= now),
                or_(BroadcastAnnouncement.ends_at.is_(None), BroadcastAnnouncement.ends_at >= now),
            )
        )
        .order_by(BroadcastAnnouncement.created_at.desc(), BroadcastAnnouncement.id.desc())
        .limit(30)
        .all()
    )
    return [_to_out(row) for row in rows]


@router.get("/broadcast/admin", response_model=list[BroadcastAnnouncementOut])
def admin_list_broadcasts(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    if not _has_table(db):
        return []
    rows = (
        db.query(BroadcastAnnouncement)
        .order_by(BroadcastAnnouncement.created_at.desc(), BroadcastAnnouncement.id.desc())
        .limit(300)
        .all()
    )
    return [_to_out(row) for row in rows]


@router.post("/broadcast/admin", response_model=BroadcastAnnouncementOut)
def admin_create_broadcast(
    payload: BroadcastAnnouncementCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not _ensure_table(db):
        raise HTTPException(status_code=503, detail="Announcements table is not ready yet")

    starts_at = _as_utc(payload.starts_at)
    ends_at = _as_utc(payload.ends_at)
    _validate_window(starts_at, ends_at)

    row = BroadcastAnnouncement(
        title=payload.title.strip(),
        message=payload.message.strip(),
        level=_coerce_level(payload.level),
        is_active=bool(payload.is_active),
        starts_at=starts_at,
        ends_at=ends_at,
        created_by_email=admin.email,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.patch("/broadcast/admin/{announcement_id}", response_model=BroadcastAnnouncementOut)
def admin_update_broadcast(
    announcement_id: int,
    payload: BroadcastAnnouncementUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    if not _has_table(db):
        raise HTTPException(status_code=404, detail="Announcement not found")

    row = db.query(BroadcastAnnouncement).filter(BroadcastAnnouncement.id == announcement_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Announcement not found")

    fields_set = set(getattr(payload, "__fields_set__", set()))
    if not fields_set:
        raise HTTPException(status_code=400, detail="Nothing to update")

    if "title" in fields_set and payload.title is not None:
        row.title = payload.title.strip()
    if "message" in fields_set and payload.message is not None:
        row.message = payload.message.strip()
    if "level" in fields_set and payload.level is not None:
        row.level = _coerce_level(payload.level)
    if "is_active" in fields_set and payload.is_active is not None:
        row.is_active = bool(payload.is_active)
    if "starts_at" in fields_set:
        row.starts_at = _as_utc(payload.starts_at)
    if "ends_at" in fields_set:
        row.ends_at = _as_utc(payload.ends_at)

    _validate_window(_as_utc(row.starts_at), _as_utc(row.ends_at))
    db.commit()
    db.refresh(row)
    return _to_out(row)
