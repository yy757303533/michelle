"""User login, user admin, and audit log APIs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc, select

from app.auth import (
    audit,
    get_current_user,
    hash_password,
    issue_token,
    require_admin,
    verify_password,
)
from app.config import settings
from app.db import get_session
from app.models import AuditLog, User

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=6, max_length=200)
    role: Literal["admin", "reviewer", "viewer"] = "viewer"


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=6, max_length=200)
    role: Literal["admin", "reviewer", "viewer"] | None = None
    is_active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


def _user_out(user: User) -> dict:
    data = user.model_dump()
    data.pop("password_hash", None)
    return data


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = (
        (await session.execute(select(User).where(User.username == body.username)))
        .scalars()
        .first()
    )
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid username or password")
    token = issue_token(user)
    await audit(
        actor={"sub": user.user_id, "username": user.username, "role": user.role},
        action="auth.login",
        session=session,
    )
    await session.commit()
    response.set_cookie(
        "michelle_session",
        token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env.lower() in {"shared", "staging", "prod", "production"},
        max_age=60 * 60 * 24 * 7,
        path="/",
    )
    return {"data": {"token": token, "user": _user_out(user)}}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie("michelle_session", path="/", samesite="lax")
    return {"data": {"ok": True}}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return {"data": user}


@router.post("/me/password")
async def change_my_password(
    body: PasswordChange,
    actor: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await session.get(User, str(actor.get("sub", "")))
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="user not found")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.updated_at = datetime.now(UTC)
    await audit(
        actor=actor,
        action="auth.password_changed",
        target_type="user",
        target_id=user.user_id,
        session=session,
    )
    await session.commit()
    return {"data": {"ok": True}}


@router.get("/users")
async def list_users(
    _admin: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (await session.execute(select(User).order_by(User.username))).scalars().all()
    return {"data": [_user_out(u) for u in rows]}


@router.post("/users", status_code=201)
async def create_user(
    body: UserCreate,
    actor: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    exists = (
        (await session.execute(select(User).where(User.username == body.username)))
        .scalars()
        .first()
    )
    if exists:
        raise HTTPException(status_code=409, detail="username already exists")
    user = User(
        user_id="u_" + uuid4().hex[:12],
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
    )
    session.add(user)
    await audit(
        actor=actor,
        action="user.created",
        target_type="user",
        target_id=user.user_id,
        detail=body.username,
        session=session,
    )
    await session.commit()
    return {"data": _user_out(user)}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdate,
    actor: dict = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.role:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    user.updated_at = datetime.now(UTC)
    await audit(
        actor=actor,
        action="user.updated",
        target_type="user",
        target_id=user_id,
        detail=user.username,
        session=session,
    )
    await session.commit()
    return {"data": _user_out(user)}


@router.get("/audit")
async def list_audit_logs(
    _admin: dict = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        (await session.execute(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)))
        .scalars()
        .all()
    )
    return {"data": [r.model_dump() for r in rows]}
