"""Users and audit logs for internal rollout."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models._types import TZDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(SQLModel, table=True):
    __tablename__ = "users"

    user_id: str = Field(primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: str = "viewer"  # admin | reviewer | viewer
    is_active: bool = True
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )


class ProjectMember(SQLModel, table=True):
    __tablename__ = "project_members"

    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str = "viewer"  # viewer | reviewer | admin
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    audit_id: str = Field(primary_key=True)
    actor_user_id: str = ""
    actor_username: str = ""
    actor_role: str = ""
    action: str
    method: str = ""
    path: str = ""
    status_code: int = 0
    target_type: str = ""
    target_id: str = ""
    detail: str = ""
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(TZDateTime(), nullable=False),
    )
