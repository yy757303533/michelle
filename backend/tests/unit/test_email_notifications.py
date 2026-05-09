"""Email notification service tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.models import RuntimeSetting
from app.services.email_notifications import _split_recipients, send_test_email


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def test_split_recipients_accepts_common_separators():
    assert _split_recipients("a@example.com, b@example.com\nc@example.com;d@example.com") == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
        "d@example.com",
    ]


@pytest.mark.asyncio
async def test_send_test_email_validates_required_settings(session):
    session.add(RuntimeSetting(key="email_enabled", value="true"))
    await session.commit()

    result = await send_test_email(session=session)

    assert result["ok"] is False
    assert "smtp_host" in result["detail"]


@pytest.mark.asyncio
async def test_send_test_email_uses_smtp_config(session):
    rows = {
        "email_enabled": "true",
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "smtp_username": "user",
        "smtp_password": "secret",
        "smtp_from": "michelle@example.com",
        "smtp_to": "ops@example.com",
        "smtp_use_tls": "true",
        "smtp_use_ssl": "false",
    }
    for key, value in rows.items():
        session.add(RuntimeSetting(key=key, value=value))
    await session.commit()

    with patch("app.services.email_notifications._send_sync") as send_sync:
        result = await send_test_email(session=session)

    assert result["ok"] is True
    assert "ops@example.com" in result["detail"]
    send_sync.assert_called_once()
