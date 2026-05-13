from __future__ import annotations

import subprocess

from app.config import settings
from app.services.dev_context.server_logs import collect_server_logs, server_log_security_findings


def test_collect_server_logs_uses_configured_paths_only(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "michelle_server_logs_json",
        """
        {"servers":[{"name":"staging-api","host":"10.0.0.1","user":"readonly",
        "env":"staging","roles":["api"],"log_paths":["/var/log/app.log"]}]}
        """,
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="line1\nline2", stderr="")

    result = collect_server_logs(runner=fake_run)

    assert result["configured"] is True
    assert result["snippets"][0]["server"] == "staging-api"
    assert result["snippets"][0]["path"] == "/var/log/app.log"
    assert result["snippets"][0]["text"] == "line1\nline2"
    assert calls[0][:3] == ["ssh", "-o", "BatchMode=yes"]
    assert "tail -n 200 -- /var/log/app.log" in calls[0][-1]


def test_collect_server_logs_redacts_sensitive_output(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "michelle_server_logs_json",
        '{"servers":[{"name":"staging-api","host":"10.0.0.1","user":"readonly",'
        '"log_paths":["/var/log/app.log"]}]}',
    )

    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="Authorization: Bearer abc123\npassword=secret token=tokvalue\n",
            stderr="",
        )

    result = collect_server_logs(runner=fake_run)

    text = result["snippets"][0]["text"]
    assert "abc123" not in text
    assert "secret" not in text
    assert "tokvalue" not in text
    assert "Authorization: Bearer [REDACTED]" in text


def test_server_log_security_findings_reject_unsafe_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "michelle_server_logs_json",
        '{"servers":[{"name":"bad","host":"10.0.0.1","user":"readonly",'
        '"log_paths":["/var/log/app.log","/var/log/../secret"]}]}',
    )

    findings = server_log_security_findings()

    assert findings == ["bad: unsafe log path '/var/log/../secret'"]
