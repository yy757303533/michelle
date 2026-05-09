"""Reset a Michelle user's password.

Usage:
  uv run python scripts/reset_admin_password.py --password 'new-password'
  uv run python scripts/reset_admin_password.py --username admin --password 'new-password'
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth import hash_password  # noqa: E402
from app.db import async_session_maker  # noqa: E402
from app.models import User  # noqa: E402


async def reset_password(username: str, password: str) -> None:
    async with async_session_maker() as session:
        from sqlmodel import select

        user = (
            (await session.execute(select(User).where(User.username == username))).scalars().first()
        )
        if user is None:
            raise SystemExit(f"user {username!r} not found")
        user.password_hash = hash_password(password)
        user.is_active = True
        await session.commit()
        print(f"password reset for {username}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    password = args.password or getpass.getpass("New password: ")
    if len(password) < 6:
        raise SystemExit("password must be at least 6 characters")
    asyncio.run(reset_password(args.username, password))


if __name__ == "__main__":
    main()
