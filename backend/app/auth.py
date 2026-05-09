"""Local user authentication and role checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.config import settings
from app.db import async_session_maker
from app.models import AuditLog, ProjectMember, User

ROLES = {"admin", "reviewer", "viewer"}
ROLE_RANK = {"viewer": 1, "reviewer": 2, "admin": 3}
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7
INSECURE_DEFAULT_ADMIN_PASSWORD = "michelle-dev"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return (
        "pbkdf2_sha256$120000$"
        + base64.urlsafe_b64encode(salt).decode()
        + "$"
        + base64.urlsafe_b64encode(dk).decode()
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        _algo, iterations, salt_b64, hash_b64 = encoded.split("$", 3)
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(hash_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _secret() -> bytes:
    return (
        settings.admin_token
        or settings.default_admin_password
        or "michelle-local-dev-session-secret"
    ).encode()


def admin_security_findings() -> list[str]:
    findings: list[str] = []
    if settings.default_admin_password == INSECURE_DEFAULT_ADMIN_PASSWORD:
        findings.append("DEFAULT_ADMIN_PASSWORD still uses the dev default")
    if not settings.admin_token:
        findings.append("ADMIN_TOKEN is empty; no break-glass token is configured")
    return findings


def assert_shared_admin_config_safe() -> None:
    env = settings.app_env.lower()
    if env in {"prod", "production", "staging", "shared"} and admin_security_findings():
        raise RuntimeError(
            "unsafe admin config for shared environment: " + "; ".join(admin_security_findings())
        )


def issue_token(user: User) -> str:
    payload = {
        "sub": user.user_id,
        "username": user.username,
        "role": user.role,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + sig


def verify_token(token: str) -> dict | None:
    if not token:
        return None
    try:
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body.encode()))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def token_from_request(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.cookies.get("michelle_session", "")
        or request.headers.get("X-Michelle-Session", "")
        or request.headers.get("X-Michelle-Admin-Token", "")
    )


async def user_from_request(request: Request) -> dict | None:
    token = token_from_request(request)
    payload = verify_token(token)
    if payload:
        return payload
    if settings.admin_token and token == settings.admin_token:
        return {"sub": "admin-token", "username": "admin-token", "role": "admin"}
    return None


def require_role(user: dict | None, role: str) -> None:
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    if ROLE_RANK.get(str(user.get("role")), 0) < ROLE_RANK[role]:
        raise HTTPException(status_code=403, detail=f"{role} role required")


def _is_global_admin(user: dict | None) -> bool:
    return str((user or {}).get("role", "")) == "admin"


async def accessible_project_ids(user: dict | None, session: AsyncSession) -> list[str] | None:
    """Return None for global admins, otherwise explicit project memberships."""
    if settings.app_env == "test":
        return None
    if _is_global_admin(user):
        return None
    user_id = str((user or {}).get("sub", ""))
    if not user_id:
        return []
    rows = (
        (await session.execute(select(ProjectMember).where(ProjectMember.user_id == user_id)))
        .scalars()
        .all()
    )
    return [r.project_id for r in rows]


async def require_project_role(
    user: dict | None,
    project_id: str,
    min_role: str,
    session: AsyncSession,
) -> None:
    if settings.app_env == "test":
        return
    if _is_global_admin(user):
        return
    require_role(user, "viewer")
    user_id = str((user or {}).get("sub", ""))
    row = (
        (
            await session.execute(
                select(ProjectMember).where(
                    ProjectMember.user_id == user_id,
                    ProjectMember.project_id == project_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None or ROLE_RANK.get(row.role, 0) < ROLE_RANK[min_role]:
        raise HTTPException(status_code=403, detail=f"{min_role} project access required")


async def get_current_user(request: Request) -> dict:
    user = await user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    return user


async def require_admin(request: Request) -> dict:
    user = await get_current_user(request)
    require_role(user, "admin")
    return user


async def ensure_bootstrap_admin() -> None:
    assert_shared_admin_config_safe()
    async with async_session_maker() as session:
        existing = (
            (
                await session.execute(
                    select(User).where(User.username == settings.default_admin_username)
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            if (
                settings.default_admin_password
                and settings.default_admin_password != INSECURE_DEFAULT_ADMIN_PASSWORD
                and verify_password(INSECURE_DEFAULT_ADMIN_PASSWORD, existing.password_hash)
            ):
                existing.password_hash = hash_password(settings.default_admin_password)
                await session.commit()
            return
        password = _bootstrap_admin_password()
        session.add(
            User(
                user_id="u_" + uuid4().hex[:12],
                username=settings.default_admin_username,
                password_hash=hash_password(password),
                role="admin",
                is_active=True,
            )
        )
        await session.commit()


def _bootstrap_admin_password() -> str:
    if settings.default_admin_password:
        return settings.default_admin_password
    path = settings.artifacts_path / "bootstrap-admin.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("password: "):
                return line.removeprefix("password: ").strip()
    password = secrets.token_urlsafe(18)
    path.write_text(
        "Michelle bootstrap admin\n"
        f"username: {settings.default_admin_username}\n"
        f"password: {password}\n\n"
        "This password is only written because no DEFAULT_ADMIN_PASSWORD was configured. "
        "Login once, create named users, then delete this file.\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return password


async def audit(
    *,
    actor: dict | None,
    action: str,
    method: str = "",
    path: str = "",
    status_code: int = 0,
    target_type: str = "",
    target_id: str = "",
    detail: str = "",
    session: AsyncSession | None = None,
) -> None:
    row = AuditLog(
        audit_id="audit_" + uuid4().hex[:12],
        actor_user_id=str((actor or {}).get("sub", "")),
        actor_username=str((actor or {}).get("username", "")),
        actor_role=str((actor or {}).get("role", "")),
        action=action,
        method=method,
        path=path,
        status_code=status_code,
        target_type=target_type,
        target_id=target_id,
        detail=detail[:1000],
        created_at=datetime.now(UTC),
    )
    if session is not None:
        session.add(row)
        return
    async with async_session_maker() as s:
        s.add(row)
        await s.commit()
