from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.api.v1.routes import router as api_router
from app.core.config import get_settings, parse_cors_origins
import logging
from app.core.database import Base, engine
from app.core.logging import configure_logging
from app.middlewares.rate_limit import limiter


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

@app.on_event("startup")
def ensure_tables():
    if not settings.auto_create_tables:
        return

    # Optional local fallback for fresh environments.
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "DB unavailable on startup, skipping table creation: %s",
            exc,
        )

@app.get("/")
def root():
    return {"status": "ok"}
