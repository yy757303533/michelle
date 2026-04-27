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

# Clear network proxy env so httpx doesn't try to set up SOCKS/HTTP proxy
# transports during unit tests (they'd never reach the real network anyway,
# respx intercepts at the transport layer). In production, leave the env alone.
for var in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    os.environ.pop(var, None)
