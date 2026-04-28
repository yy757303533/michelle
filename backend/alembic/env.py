"""Alembic env — async SQLite + SQLModel.metadata.

Pulls the DB URL from app.config.settings, falling back to the alembic.ini
value if explicit. Lets `alembic upgrade head` work in dev without env vars.
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Make `app.*` importable regardless of where alembic is run from.
HERE = Path(__file__).resolve()
BACKEND_DIR = HERE.parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlmodel import SQLModel  # noqa: E402

# Side-effect import: registers all tables with SQLModel.metadata
import app.models  # noqa: F401, E402
from app.config import settings  # noqa: E402  (after sys.path setup)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve database URL: -x url=... > app settings > alembic.ini
x_args = context.get_x_argument(as_dictionary=True)
db_url = x_args.get("url") or settings.database_url
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite: emit batch ops for ALTER
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite: emit batch ops for ALTER
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
