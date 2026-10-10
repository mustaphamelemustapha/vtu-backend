import importlib.util
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
logger = logging.getLogger(__name__)


def _resolve_database_url(database_url: str) -> str:
    if not database_url:
        return database_url

    # Normalize legacy postgres:// scheme to postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    if not database_url.startswith("postgresql://") and not database_url.startswith("postgresql+psycopg://"):
        return database_url

    has_psycopg2 = importlib.util.find_spec("psycopg2") is not None
    has_psycopg3 = importlib.util.find_spec("psycopg") is not None
    if not has_psycopg2 and has_psycopg3 and database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    try:
        parsed = urlparse(database_url)
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        hostname = (parsed.hostname or "").lower()
        is_supabase = "supabase" in hostname

        if not is_supabase:
            # Internal/Coolify/Docker/local postgres databases do not support SSL
            query_params["sslmode"] = ["disable"]
        else:
            # Supabase managed databases require SSL
            query_params["sslmode"] = ["require"]

        flat_query = {k: v[0] if len(v) == 1 else v for k, v in query_params.items()}
        new_query = urlencode(flat_query, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
    except Exception as exc:
        logger.warning("Failed to sanitize database url: %s", exc)
        return database_url


def _build_connect_args(database_url: str) -> dict:
    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("postgresql"):
        return {}

    hostname = (parsed.hostname or "").lower()
    is_supabase = "supabase" in hostname

    connect_args = {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
        "sslmode": "require" if is_supabase else "disable",
    }
    return connect_args


database_url = _resolve_database_url(str(settings.database_url))

_pool_kwargs = {}
if database_url.startswith("postgresql"):
    configured_pool_size = int(settings.db_pool_size)
    configured_max_overflow = int(settings.db_max_overflow)
    configured_pool_timeout = int(settings.db_pool_timeout)

    # Guardrails for production stability: too-small pools cause frequent 503
    # under normal concurrent requests (auth + dashboard bootstrap + polling).
    # NOTE: Set to 5 and 5 to respect Supabase 15 connection limit, allowing 2 instances during deploy.
    pool_size = max(5, configured_pool_size)
    max_overflow = max(5, configured_max_overflow)
    pool_timeout = max(15, configured_pool_timeout)

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

from sqlalchemy.event import listens_for
import sqlite3
import datetime

@listens_for(engine, "connect")
def register_sqlite_now(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        # Register standard PostgreSQL-style now() function in SQLite
        dbapi_connection.create_function("now", 0, lambda: datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
