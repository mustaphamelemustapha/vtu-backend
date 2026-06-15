import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.core.database import SessionLocal, Base, engine
from app.main import app
from app.models import User, UserRole, BroadcastAnnouncement, AnnouncementLevel
from app.core.security import hash_password, create_access_token

Base.metadata.create_all(bind=engine)

def _seed_test_user(db, email: str, role: UserRole) -> User:
    # Clean up user if existing
    db.query(User).filter(User.email == email).delete(synchronize_session=False)
    db.commit()

    user = User(
        email=email,
        full_name="Announcement Tester",
        hashed_password=hash_password("password"),
        role=role,
        is_verified=True,
        referral_code=f"REF-{email.split('@')[0]}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def _auth_headers(user_id: int, role: str) -> dict:
    token = create_access_token(str(user_id), role)
    return {"Authorization": f"Bearer {token}"}

def _clear_announcements(db):
    db.query(BroadcastAnnouncement).delete(synchronize_session=False)
    db.commit()

def test_list_active_broadcasts():
    db = SessionLocal()
    try:
        _clear_announcements(db)
        user = _seed_test_user(db, "ann_user@example.com", UserRole.USER)
        headers = _auth_headers(user.id, "user")

        now = datetime.now(timezone.utc)

        # 1. Active announcement (no window)
        a1 = BroadcastAnnouncement(
            title="Always Active",
            message="This is always active.",
            level=AnnouncementLevel.INFO,
            is_active=True
        )
        # 2. Active announcement (inside window)
        a2 = BroadcastAnnouncement(
            title="Active Window",
            message="Inside window.",
            level=AnnouncementLevel.SUCCESS,
            is_active=True,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1)
        )
        # 3. Inactive announcement (is_active=False)
        a3 = BroadcastAnnouncement(
            title="Disabled",
            message="Disabled message.",
            level=AnnouncementLevel.WARNING,
            is_active=False
        )
        # 4. Inactive announcement (future start)
        a4 = BroadcastAnnouncement(
            title="Future Active",
            message="Starts in future.",
            level=AnnouncementLevel.CRITICAL,
            is_active=True,
            starts_at=now + timedelta(hours=2)
        )
        # 5. Inactive announcement (past end)
        a5 = BroadcastAnnouncement(
            title="Expired",
            message="Already ended.",
            level=AnnouncementLevel.INFO,
            is_active=True,
            ends_at=now - timedelta(hours=2)
        )

        db.add_all([a1, a2, a3, a4, a5])
        db.commit()

        with TestClient(app) as client:
            response = client.get("/api/v1/notifications/broadcast", headers=headers)
            assert response.status_code == 200
            items = response.json()

            # Should only receive Always Active and Active Window
            assert len(items) == 2
            titles = {item["title"] for item in items}
            assert "Always Active" in titles
            assert "Active Window" in titles

    finally:
        db.close()

def test_admin_create_broadcast_fcm():
    db = SessionLocal()
    try:
        _clear_announcements(db)
        admin = _seed_test_user(db, "ann_admin@example.com", UserRole.ADMIN)
        headers = _auth_headers(admin.id, "admin")

        payload = {
            "title": "System Upgrade",
            "message": "Maintenance tonight.",
            "level": "critical",
            "is_active": True
        }

        with patch("app.services.push_notification.PushNotificationService.send_broadcast") as mock_fcm:
            mock_fcm.return_value = True
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/notifications/broadcast/admin",
                    json=payload,
                    headers=headers
                )
                assert response.status_code == 200
                data = response.json()
                assert data["title"] == "System Upgrade"
                assert data["level"] == "critical"
                assert data["is_active"] is True

            # Verify FCM broadcast was triggered
            mock_fcm.assert_called_once_with(
                title="System Upgrade",
                body="Maintenance tonight.",
                data={"type": "announcement", "id": str(data["id"])}
            )

            # Verify saved in DB
            db_item = db.query(BroadcastAnnouncement).filter(BroadcastAnnouncement.id == data["id"]).first()
            assert db_item is not None
            assert db_item.title == "System Upgrade"
            assert db_item.level == AnnouncementLevel.CRITICAL

    finally:
        db.close()

def test_admin_update_broadcast():
    db = SessionLocal()
    try:
        _clear_announcements(db)
        admin = _seed_test_user(db, "ann_admin2@example.com", UserRole.ADMIN)
        headers = _auth_headers(admin.id, "admin")

        a1 = BroadcastAnnouncement(
            title="Initial Title",
            message="Initial Message",
            level=AnnouncementLevel.INFO,
            is_active=True
        )
        db.add(a1)
        db.commit()

        payload = {
            "title": "Updated Title",
            "message": "Updated Message",
            "level": "warning",
            "is_active": False
        }

        with TestClient(app) as client:
            response = client.patch(
                f"/api/v1/notifications/broadcast/admin/{a1.id}",
                json=payload,
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["title"] == "Updated Title"
            assert data["level"] == "warning"
            assert data["is_active"] is False

        db.refresh(a1)
        assert a1.title == "Updated Title"
        assert a1.level == AnnouncementLevel.WARNING
        assert a1.is_active is False

    finally:
        db.close()

def test_broadcast_validation_window():
    db = SessionLocal()
    try:
        _clear_announcements(db)
        admin = _seed_test_user(db, "ann_admin3@example.com", UserRole.ADMIN)
        headers = _auth_headers(admin.id, "admin")

        now = datetime.now(timezone.utc)
        starts_at = now + timedelta(hours=2)
        ends_at = now + timedelta(hours=1) # ends before starts

        payload = {
            "title": "Invalid Window",
            "message": "This will fail.",
            "level": "info",
            "is_active": True,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat()
        }

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/notifications/broadcast/admin",
                json=payload,
                headers=headers
            )
            assert response.status_code == 400
            assert "ends_at must be after starts_at" in response.json()["detail"]

    finally:
        db.close()
