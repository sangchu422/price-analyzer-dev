from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import URL, create_engine, pool

from app.core.config import settings
from app.db.base import Base
from app.db import models as _models
from app.db.sqlite import configure_sqlite


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
database_path = Path(
    config.attributes.get("database_path", settings.database_path)
)
database_url = URL.create("sqlite", database=str(database_path))


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connectable = configure_sqlite(
        create_engine(database_url, poolclass=pool.NullPool)
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
