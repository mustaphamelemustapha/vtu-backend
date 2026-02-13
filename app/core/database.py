import importlib.util
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from urllib.parse import urlparse
from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()


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

engine = create_engine(
    database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=2,
    max_overflow=0,
    pool_timeout=30,
    connect_args=_build_connect_args(database_url),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
