from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.api.v1.routes import router as api_router
from app.core.config import get_settings, parse_cors_origins
import logging
from app.core.database import Base, engine, SessionLocal
from app.core.logging import configure_logging
from app.middlewares.rate_limit import limiter
from app.models import User, UserRole


settings = get_settings()

configure_logging()

app = FastAPI(title=settings.app_name)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

configured_origins = parse_cors_origins(settings.cors_origins or "")
allow_origins = list(
    dict.fromkeys(
        configured_origins
        + [
            "https://vtu-frontend-beta.vercel.app",
            "https://vtu-frontend-git-main-mmt-ech-globe.vercel.app",
        ]
    )
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
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

@app.get("/")
def root():
    return {"status": "ok"}
