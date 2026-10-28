"""Alembic environment: DATABASE_URL is the only source of connection details."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from redline.storage.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = (os.environ.get("DATABASE_URL") or "").strip()
if not database_url:
    raise SystemExit("DATABASE_URL absent : migration refusée")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        is_migrator = connection.execute(text(
            "SELECT current_user = 'redline_migrator'"
        )).scalar_one()
        if is_migrator:
            connection.execute(text("SET ROLE redline_owner"))
        # The reads above open an implicit SQLAlchemy transaction. Alembic must start its
        # own DDL transaction, otherwise the outer context silently rolls back migration
        # and version when the connection is returned.
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, include_schemas=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
