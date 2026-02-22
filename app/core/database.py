import importlib.util
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from urllib.parse import urlparse
from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
logger = logging.getLogger(__name__)


def _resolve_database_url(database_url: str) -> str:
    if not database_url.startswith("postgresql://"):
        return database_url
    has_psycopg2 = importlib.util.find_spec("psycopg2") is not None
    has_psycopg3 = importlib.util.find_spec("psycopg") is not None
    if not has_psycopg2 and has_psycopg3:
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def _build_connect_args(database_url: str) -> dict:
    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("postgresql"):
        return {}

    connect_args = {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
    local_hosts = {"localhost", "127.0.0.1", "db"}
    if parsed.hostname not in local_hosts:
        connect_args["sslmode"] = "require"
    return connect_args


database_url = _resolve_database_url(str(settings.database_url))

_pool_kwargs = {}
if database_url.startswith("postgresql"):
    configured_pool_size = int(settings.db_pool_size)
    configured_max_overflow = int(settings.db_max_overflow)
    configured_pool_timeout = int(settings.db_pool_timeout)

    # Guardrails for production stability: too-small pools cause frequent 503
    # under normal concurrent requests (auth + dashboard bootstrap + polling).
    pool_size = max(5, configured_pool_size)
    max_overflow = max(5, configured_max_overflow)
    pool_timeout = max(8, configured_pool_timeout)

    if (
        pool_size != configured_pool_size
        or max_overflow != configured_max_overflow
        or pool_timeout != configured_pool_timeout
    ):
        logger.warning(
            "Adjusted DB pool settings for stability: pool_size %s->%s, max_overflow %s->%s, pool_timeout %s->%s",
            configured_pool_size,
            pool_size,
            configured_max_overflow,
            max_overflow,
            configured_pool_timeout,
            pool_timeout,
        )

    _pool_kwargs = {
        "pool_pre_ping": settings.db_pool_pre_ping,
        "pool_recycle": settings.db_pool_recycle,
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_timeout": pool_timeout,
        # Reuse hot connections first to reduce churn under burst traffic.
        "pool_use_lifo": True,
    }

engine = create_engine(
    database_url,
    **_pool_kwargs,
    connect_args=_build_connect_args(database_url),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
