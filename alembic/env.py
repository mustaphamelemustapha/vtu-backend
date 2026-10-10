import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context
from app.core.config import get_settings
from app.core.database import Base, _resolve_database_url, _build_connect_args
from app import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
resolved_db_url = _resolve_database_url(str(settings.database_url))
config.set_main_option("sqlalchemy.url", resolved_db_url)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(render_as_batch=True, 
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    from sqlalchemy import create_engine
    connectable = create_engine(
        resolved_db_url,
        poolclass=pool.NullPool,
        connect_args=_build_connect_args(resolved_db_url),
    )

    with connectable.connect() as connection:
        context.configure(render_as_batch=True, connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
