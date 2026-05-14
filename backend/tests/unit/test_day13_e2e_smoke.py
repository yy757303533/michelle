from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_smoke_module():
    script = Path(__file__).resolve().parents[3] / "scripts" / "day13_e2e_smoke.py"
    spec = importlib.util.spec_from_file_location("day13_e2e_smoke", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_assert_finished_diagnosis_job_rejects_done_without_diag_id() -> None:
    smoke = _load_smoke_module()

    with pytest.raises(RuntimeError, match="missing diag_id"):
        smoke._assert_finished_diagnosis_job(
            {"job_id": "diagjob_x", "status": "done", "diag_id": "", "error": ""}
        )


def test_assert_finished_diagnosis_job_returns_done_job_with_diag_id() -> None:
    smoke = _load_smoke_module()

    data = {"job_id": "diagjob_x", "status": "done", "diag_id": "diag_x", "error": ""}

    assert smoke._assert_finished_diagnosis_job(data) == data
