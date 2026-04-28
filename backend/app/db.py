"""Async SQLite via SQLModel.

Schema is owned by Alembic — `init_db()` runs `alembic upgrade head` on startup.
For unit tests with `:memory:` URLs, we fall back to `metadata.create_all`
because alembic-on-memory is fiddly and not worth it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.config import settings
from app.obs import get_logger

_log = get_logger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Bring the DB to head.

    For SQLite file-backed DBs: run `alembic upgrade head` (subprocess).
    For SQLite in-memory (used by tests): metadata.create_all on the live engine.
    """
    # Always import models first so SQLModel.metadata is populated
    from app import models  # noqa: F401

    if ":memory:" in settings.database_url:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        _log.info("db.initialized.memory", url=settings.database_url)
        return

    Path("./data").mkdir(parents=True, exist_ok=True)
    await _run_alembic_upgrade()
    _log.info("db.initialized.alembic", url=settings.database_url)


async def _run_alembic_upgrade() -> None:
    """Spawn `alembic upgrade head`.

    Compatibility: if the DB already has business tables but no alembic_version,
    stamp head first so we don't try to CREATE TABLE on a populated DB.
    """
    import asyncio

    try:
        async with engine.connect() as conn:
            ver = await conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            )
            has_version = ver.fetchone() is not None
            biz = await conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
            )
            has_biz = biz.fetchone() is not None
        if has_biz and not has_version:
            _log.info("db.alembic.stamp_existing_db_at_head")
            stamp = await asyncio.create_subprocess_exec(
                "uv",
                "run",
                "alembic",
                "stamp",
                "head",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await stamp.communicate()
    except Exception as e:
        _log.warning("db.alembic.stamp_check_failed", error=str(e)[:200])

    proc = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "alembic",
        "upgrade",
        "head",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        _log.error(
            "db.alembic.failed",
            stdout=stdout.decode()[:500],
            stderr=stderr.decode()[:500],
        )
        raise RuntimeError(f"alembic upgrade failed: {(stderr or stdout).decode()[:500]}")
    _log.debug("db.alembic.ok", out=stdout.decode()[-200:])


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
