"""Local filesystem storage for artifacts (screenshots, reports, traces).

MVP uses local FS; Phase 2 swaps to MinIO/S3 via the same interface.
"""

from __future__ import annotations

from pathlib import Path

from app.config import settings


def artifacts_root() -> Path:
    """Root directory for run artifacts."""
    p = settings.artifacts_path
    return p


def run_dir(project_id: str, run_id: str) -> Path:
    """Per-run subdirectory: artifacts/<project_id>/<run_id>/"""
    d = artifacts_root() / project_id / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "screenshots").mkdir(exist_ok=True)
    return d
