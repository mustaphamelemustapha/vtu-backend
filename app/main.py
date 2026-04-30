from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text, inspect
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from app.api.v1.routes import router as api_router
from app.core.config import get_settings, parse_cors_origins
import logging
import time
from urllib.parse import urlparse
from app.core.database import Base, engine, SessionLocal
from app.core.logging import configure_logging
from app.middlewares.rate_limit import limiter
from app.models import User, UserRole
from app.services.pending_reconcile import start_pending_reconcile_worker, stop_pending_reconcile_worker


settings = get_settings()

configure_logging()

app = FastAPI(title=settings.app_name)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
_started_at = time.time()


@app.exception_handler(SQLAlchemyTimeoutError)
async def sqlalchemy_timeout_handler(request, exc):
    logging.getLogger(__name__).warning("Database pool timeout on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Service is busy. Please retry in a moment."},
    )

configured_origins = parse_cors_origins(settings.cors_origins or "")
def _origin_from_url(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


frontend_origin = _origin_from_url(settings.frontend_base_url)
allow_origins = list(
    dict.fromkeys(
        configured_origins
        + [
            "https://vtu-frontend-beta.vercel.app",
            "https://vtu-frontend-git-main-mmt-ech-globe.vercel.app",
            "https://axisvtu.vercel.app",
            "https://axisvtu.com",
            "https://www.axisvtu.com",
            *([frontend_origin] if frontend_origin else []),
        ]
    )
)
allow_origin_regex = (
    r"^https:\/\/(?:vtu-frontend|axisvtu)(?:-[A-Za-z0-9-]+)?\.vercel\.app$"
    r"|^https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?$"
)

logging.getLogger(__name__).info(
    "CORS allow_origins=%s allow_origin_regex=%s",
    allow_origins,
    allow_origin_regex,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


def _bootstrap_admins() -> None:
    raw = (settings.bootstrap_admin_emails or "").strip()
    if not raw:
        return

    emails = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not emails:
        return

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        updated = 0
        missing: list[str] = []
        for email in emails:
            user = db.query(User).filter(User.email == email).first()
            if not user:
                missing.append(email)
                continue
            if user.role != UserRole.ADMIN:
                user.role = UserRole.ADMIN
                updated += 1
        if updated:
            db.commit()
        if updated:
            logger.info("Bootstrapped admin role for %s user(s).", updated)
        if missing:
            logger.warning("BOOTSTRAP_ADMIN_EMAILS users not found: %s", ", ".join(missing))
    except Exception as exc:
        logger.warning("Admin bootstrap failed: %s", exc)
    finally:
        db.close()


@app.on_event("startup")
def ensure_tables():
    if not settings.auto_create_tables:
        _bootstrap_admins()
        _ensure_user_phone_column()
        _ensure_user_security_pin_columns()
        _ensure_transaction_recipient_column()
        start_pending_reconcile_worker()
        return

    # Optional local fallback for fresh environments.
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "DB unavailable on startup, skipping table creation: %s",
            exc,
        )
    _bootstrap_admins()
    _ensure_user_phone_column()
    _ensure_user_security_pin_columns()
    _ensure_transaction_recipient_column()
    start_pending_reconcile_worker()


@app.on_event("shutdown")
def shutdown_workers():
    stop_pending_reconcile_worker()

@app.get("/")
def root():
    return {"status": "ok"}


def _ensure_user_phone_column() -> None:
    try:
        inspector = inspect(engine)
        if not inspector.has_table("users"):
            return
        cols = {c["name"] for c in inspector.get_columns("users")}
        if "phone_number" in cols:
            return
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN phone_number VARCHAR(32)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_phone_number ON users (phone_number)"))
        logging.getLogger(__name__).info("Added users.phone_number column for phone login.")
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not ensure phone_number column: %s", exc)


def _ensure_user_security_pin_columns() -> None:
    try:
        inspector = inspect(engine)
        if not inspector.has_table("users"):
            return
        cols = {c["name"] for c in inspector.get_columns("users")}
        dialect_name = getattr(engine.dialect, "name", "")
        ts_type = "TIMESTAMP WITH TIME ZONE" if dialect_name == "postgresql" else "DATETIME"
        statements: list[str] = []
        if "pin_hash" not in cols:
            statements.append("ALTER TABLE users ADD COLUMN pin_hash VARCHAR(255)")
        if "pin_set_at" not in cols:
            statements.append(f"ALTER TABLE users ADD COLUMN pin_set_at {ts_type}")
        if "pin_failed_attempts" not in cols:
            statements.append("ALTER TABLE users ADD COLUMN pin_failed_attempts INTEGER NOT NULL DEFAULT 0")
        if "pin_locked_until" not in cols:
            statements.append(f"ALTER TABLE users ADD COLUMN pin_locked_until {ts_type}")
        if "pin_reset_token_hash" not in cols:
            statements.append("ALTER TABLE users ADD COLUMN pin_reset_token_hash VARCHAR(255)")
        if "pin_reset_token_expires_at" not in cols:
            statements.append(f"ALTER TABLE users ADD COLUMN pin_reset_token_expires_at {ts_type}")

        if not statements:
            return

        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_users_pin_reset_token_hash ON users (pin_reset_token_hash)")
            )
        logging.getLogger(__name__).info("Added users transaction PIN columns for backend security.")
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not ensure transaction pin columns: %s", exc)


def _ensure_transaction_recipient_column() -> None:
    try:
        inspector = inspect(engine)
        if not inspector.has_table("transactions"):
            return
        cols = {c["name"] for c in inspector.get_columns("transactions")}
        if "recipient_phone" in cols:
            return
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN recipient_phone VARCHAR(32)"))
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_transactions_recipient_phone ON transactions (recipient_phone)")
            )
        logging.getLogger(__name__).info("Added transactions.recipient_phone for safer reconciliation.")
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not ensure recipient_phone column: %s", exc)


@app.get("/healthz")
@app.head("/healthz")
def healthz():
    # Liveness: process is up.
    return {
        "status": "ok",
        "uptime_seconds": int(max(0, time.time() - _started_at)),
        "service": settings.app_name,
    }


@app.get("/readyz")
def readyz():
    # Readiness: database is reachable.
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "uptime_seconds": int(max(0, time.time() - _started_at)),
        }
    except Exception as exc:
        logging.getLogger(__name__).warning("Readiness DB check failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "detail": "database_unavailable"},
        )
    finally:
        db.close()
