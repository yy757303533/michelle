from __future__ import annotations

import pytest


def test_shared_env_rejects_default_admin_password(monkeypatch) -> None:
    from app.auth import assert_shared_admin_config_safe
    from app.config import settings

    monkeypatch.setattr(settings, "app_env", "shared")
    monkeypatch.setattr(settings, "default_admin_password", "michelle-dev")
    monkeypatch.setattr(settings, "admin_token", "")

    with pytest.raises(RuntimeError, match="unsafe admin config"):
        assert_shared_admin_config_safe()


def test_shared_env_accepts_changed_admin_password(monkeypatch) -> None:
    from app.auth import assert_shared_admin_config_safe
    from app.config import settings

    monkeypatch.setattr(settings, "app_env", "shared")
    monkeypatch.setattr(settings, "default_admin_password", "a-strong-password")
    monkeypatch.setattr(settings, "admin_token", "break-glass")

    assert_shared_admin_config_safe()


def test_empty_bootstrap_password_generates_local_file(monkeypatch, tmp_path) -> None:
    import app.auth as auth_mod
    from app.config import settings

    monkeypatch.setattr(settings, "default_admin_password", "")
    monkeypatch.setattr(settings, "default_admin_username", "admin")
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path))
    monkeypatch.setattr(auth_mod.secrets, "token_urlsafe", lambda _n: "generated-password")

    password = auth_mod._bootstrap_admin_password()

    assert password == "generated-password"
    text = (tmp_path / "bootstrap-admin.txt").read_text(encoding="utf-8")
    assert "username: admin" in text
    assert "password: generated-password" in text


def test_empty_bootstrap_password_reuses_existing_file(monkeypatch, tmp_path) -> None:
    import app.auth as auth_mod
    from app.config import settings

    (tmp_path / "bootstrap-admin.txt").write_text(
        "Michelle bootstrap admin\nusername: admin\npassword: existing-password\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "default_admin_password", "")
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path))
    monkeypatch.setattr(auth_mod.secrets, "token_urlsafe", lambda _n: "new-password")

    assert auth_mod._bootstrap_admin_password() == "existing-password"


@pytest.mark.asyncio
async def test_login_sets_session_cookie(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel

    import app.db as db_mod
    from app.auth import hash_password
    from app.main import app
    from app.models import User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session_maker", maker)
    async with maker() as s:
        s.add(
            User(
                user_id="u1",
                username="admin",
                password_hash=hash_password("password"),
                role="admin",
                is_active=True,
            )
        )
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/auth/login", json={"username": "admin", "password": "password"})
        assert r.status_code == 200
        assert "michelle_session=" in r.headers.get("set-cookie", "")
        me = await ac.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["data"]["username"] == "admin"

    await engine.dispose()
