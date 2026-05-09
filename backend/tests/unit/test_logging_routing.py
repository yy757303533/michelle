from __future__ import annotations

import json
import logging
from pathlib import Path


def _flush_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def _json_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_setup_logging_writes_total_and_domain_logs(tmp_path, monkeypatch):
    from app.config import settings
    from app.obs import EVENTS
    from app.obs.logger import get_logger, setup_logging

    log_dir = tmp_path / "logs"
    monkeypatch.setattr(settings, "log_file", str(log_dir / "michelle.log"))
    monkeypatch.setattr(settings, "log_max_bytes", 1024 * 1024)
    monkeypatch.setattr(settings, "log_backup_count", 1)

    setup_logging()
    log = get_logger("test.logging")
    log.info(EVENTS.PRD_UPLOADED.name, prd_id="p1", chapter_count=2, hash="abc")
    log.info("case.generation.started", job_id="j1", prd_id="p1")
    log.info(EVENTS.RUN_COMPLETED.name, run_id="r1", status="passed", duration_ms=12)
    log.info(EVENTS.DIAGNOSIS_GENERATED.name, diag_id="d1", run_id="r1", category="case_issue", confidence=0.8)
    log.info("settings.runtime_updated", keys=["smtp_password"], smtp_password="plain-secret")
    _flush_handlers()

    assert (log_dir / "michelle.log").exists()
    assert {row["event"] for row in _json_lines(log_dir / "michelle.log")} >= {
        "prd.uploaded",
        "case.generation.started",
        "run.completed",
        "diagnosis.generated",
        "settings.runtime_updated",
    }
    assert _json_lines(log_dir / "prd_upload.log")[0]["event"] == "prd.uploaded"
    assert _json_lines(log_dir / "case_generation.log")[0]["event"] == "case.generation.started"
    assert _json_lines(log_dir / "case_execution.log")[0]["event"] == "run.completed"
    assert _json_lines(log_dir / "diagnosis.log")[0]["event"] == "diagnosis.generated"

    settings_rows = _json_lines(log_dir / "settings.log")
    assert settings_rows[0]["event"] == "settings.runtime_updated"
    assert settings_rows[0]["smtp_password"] == "***"
    assert "plain-secret" not in (log_dir / "settings.log").read_text(encoding="utf-8")
