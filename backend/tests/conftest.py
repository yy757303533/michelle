"""Shared pytest fixtures."""

import os
from pathlib import Path

# Ensure tests don't pollute the dev .env
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ARTIFACTS_DIR", str(Path("./artifacts-test")))
os.environ.setdefault("MINIMAX_API_KEY", "")
os.environ.setdefault("FLYWHEEL_TOKEN", "")
os.environ.setdefault("LOGFIRE_TOKEN", "")
