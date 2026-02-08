from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.api.v1.routes import router as api_router
from app.core.config import get_settings
import logging
from app.core.database import Base, engine
from app.core.logging import configure_logging
from app.middlewares.rate_limit import limiter


settings = get_settings()

configure_logging()

app = FastAPI(title=settings.app_name)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

configured_origins = [
    origin.strip()
    for origin in (settings.cors_origins or "").split(",")
    if origin.strip()
]
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
    # Create tables in fresh environments to avoid 500s on first use.
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "DB unavailable on startup, skipping table creation: %s",
            exc,
        )

@app.options("/{full_path:path}")
async def preflight_handler(full_path: str, request: Request):
    origin = request.headers.get("origin", "https://vtu-frontend-beta.vercel.app")
    response = Response(status_code=204)
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Authorization,Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return response


@app.get("/")

def root():
    return {"status": "ok"}
