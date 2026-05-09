"""SMTP email notifications for run and diagnosis lifecycle events."""

from __future__ import annotations

import asyncio
import re
import smtplib
from email.message import EmailMessage
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Diagnosis, Run, TestCase
from app.obs import get_logger
from app.runtime_config import get_email_config

log = get_logger(__name__)


def _split_recipients(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n;]+", raw or "") if p.strip()]


def _validate_config(
    cfg: dict[str, Any],
    *,
    require_enabled: bool = True,
) -> tuple[bool, str, list[str]]:
    recipients = _split_recipients(cfg.get("to_addrs", ""))
    if require_enabled and not cfg.get("enabled"):
        return False, "email notifications are disabled", recipients
    if not cfg.get("host"):
        return False, "smtp_host is required", recipients
    if not cfg.get("from_addr"):
        return False, "smtp_from is required", recipients
    if not recipients:
        return False, "smtp_to is required", recipients
    if cfg.get("use_ssl") and cfg.get("use_tls"):
        return False, "choose either smtp_use_ssl or smtp_use_tls, not both", recipients
    return True, "ok", recipients


async def send_test_email(*, session: AsyncSession) -> dict[str, Any]:
    cfg = await get_email_config(session)
    ok, detail, recipients = _validate_config(cfg, require_enabled=False)
    if not ok:
        return {"ok": False, "detail": detail}
    try:
        await _send_email(
            cfg,
            recipients=recipients,
            subject="Test email",
            body=(
                "Michelle email notifications are configured correctly.\n\n"
                "You will receive future messages when enabled lifecycle events finish."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("email.test.failed", error=str(exc)[:300])
        return {"ok": False, "detail": str(exc)[:300]}
    return {"ok": True, "detail": f"sent to {', '.join(recipients)}"}


async def send_test_webhook(*, session: AsyncSession) -> dict[str, Any]:
    cfg = await get_email_config(session)
    if not cfg.get("webhook_url"):
        return {"ok": False, "detail": "webhook_url is required"}
    try:
        await _send_webhook(
            cfg, title="Michelle test webhook", text="Webhook notifications are configured."
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:300]}
    return {"ok": True, "detail": "webhook sent"}


async def notify_run_completed(*, run_id: str, session: AsyncSession) -> None:
    cfg = await get_email_config(session)
    if not cfg.get("on_run_completed"):
        return
    ok, detail, recipients = _validate_config(cfg)
    if not ok:
        if cfg.get("enabled"):
            log.warning("email.run.skipped", run_id=run_id, reason=detail)
        return

    run = await session.get(Run, run_id)
    if run is None:
        return
    case = await session.get(TestCase, run.case_id)
    subject = f"Run {run.status}: {case.name if case else run.case_id}"
    body = _run_body(run, case)
    await _send_webhook_best_effort(cfg, title=subject, text=body, run_id=run_id)
    await _send_best_effort(cfg, recipients=recipients, subject=subject, body=body, run_id=run_id)


async def notify_diagnosis_generated(*, diag_id: str, session: AsyncSession) -> None:
    cfg = await get_email_config(session)
    if not cfg.get("on_diagnosis_generated"):
        return
    ok, detail, recipients = _validate_config(cfg)
    if not ok:
        if cfg.get("enabled"):
            log.warning("email.diagnosis.skipped", diag_id=diag_id, reason=detail)
        return

    diag = await session.get(Diagnosis, diag_id)
    if diag is None:
        return
    run = await session.get(Run, diag.run_id)
    case = await session.get(TestCase, diag.case_id)
    subject = f"Diagnosis {diag.category}: {case.name if case else diag.case_id}"
    body = _diagnosis_body(diag, run, case)
    await _send_webhook_best_effort(
        cfg, title=subject, text=body, run_id=diag.run_id, diag_id=diag_id
    )
    await _send_best_effort(
        cfg,
        recipients=recipients,
        subject=subject,
        body=body,
        run_id=diag.run_id,
        diag_id=diag_id,
    )


async def _send_webhook_best_effort(
    cfg: dict[str, Any],
    *,
    title: str,
    text: str,
    run_id: str | None = None,
    diag_id: str | None = None,
) -> None:
    if not (cfg.get("webhook_enabled") and cfg.get("webhook_url")):
        return
    try:
        await _send_webhook(cfg, title=title, text=text)
        log.info("webhook.sent", title=title, run_id=run_id, diag_id=diag_id)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "webhook.send_failed", title=title, run_id=run_id, diag_id=diag_id, error=str(exc)[:300]
        )


async def _send_webhook(cfg: dict[str, Any], *, title: str, text: str) -> None:
    import httpx

    kind = cfg.get("webhook_kind") or "generic"
    if kind == "feishu":
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    elif kind == "wecom":
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    else:
        payload = {"title": title, "text": text, "source": "michelle"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(str(cfg["webhook_url"]), json=payload)
        r.raise_for_status()


async def _send_best_effort(
    cfg: dict[str, Any],
    *,
    recipients: list[str],
    subject: str,
    body: str,
    run_id: str | None = None,
    diag_id: str | None = None,
) -> None:
    try:
        await _send_email(cfg, recipients=recipients, subject=subject, body=body)
        log.info(
            "email.sent",
            subject=subject,
            recipients=len(recipients),
            run_id=run_id,
            diag_id=diag_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "email.send_failed",
            subject=subject,
            run_id=run_id,
            diag_id=diag_id,
            error=str(exc)[:300],
        )


async def _send_email(
    cfg: dict[str, Any],
    *,
    recipients: list[str],
    subject: str,
    body: str,
) -> None:
    prefix = str(cfg.get("subject_prefix") or "").strip()
    full_subject = f"{prefix} {subject}".strip()
    msg = EmailMessage()
    msg["Subject"] = full_subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    await asyncio.to_thread(_send_sync, cfg, recipients, msg)


def _send_sync(cfg: dict[str, Any], recipients: list[str], msg: EmailMessage) -> None:
    host = str(cfg["host"])
    port = int(cfg["port"])
    username = str(cfg.get("username") or "")
    password = str(cfg.get("password") or "")

    smtp_cls = smtplib.SMTP_SSL if cfg.get("use_ssl") else smtplib.SMTP
    with smtp_cls(host, port, timeout=20) as smtp:
        if cfg.get("use_tls") and not cfg.get("use_ssl"):
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(msg, to_addrs=recipients)


def _run_body(run: Run, case: TestCase | None) -> str:
    lines = [
        "Michelle run completed.",
        "",
        f"Run ID: {run.run_id}",
        f"Case ID: {run.case_id}",
        f"Case: {case.name if case else '(missing case row)'}",
        f"Project ID: {run.project_id}",
        f"Status: {run.status}",
        f"Duration: {run.duration_ms if run.duration_ms is not None else '-'} ms",
    ]
    if run.error_message:
        lines.extend(["", "Error:", run.error_message])
    if run.report_html_path:
        lines.extend(["", f"Report: {run.report_html_path}"])
    return "\n".join(lines)


def _diagnosis_body(diag: Diagnosis, run: Run | None, case: TestCase | None) -> str:
    lines = [
        "Michelle AI diagnosis finished.",
        "",
        f"Diagnosis ID: {diag.diag_id}",
        f"Run ID: {diag.run_id}",
        f"Case ID: {diag.case_id}",
        f"Case: {case.name if case else '(missing case row)'}",
        f"Run status: {run.status if run else '(missing run row)'}",
        f"Category: {diag.category}",
        f"Confidence: {diag.confidence:.2f}",
        f"Model: {diag.diagnoser_model}",
        "",
        "Reasoning:",
        diag.reasoning or "-",
        "",
        "Suggested fix:",
        diag.fix_suggestion or "-",
    ]
    return "\n".join(lines)
