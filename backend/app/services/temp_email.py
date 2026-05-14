"""Temporary email inbox support for end-to-end registration flows."""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

CODE_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")


class TempEmailError(RuntimeError):
    pass


@dataclass
class TempInbox:
    inbox_id: str
    address: str
    password: str
    token: str
    provider: str


async def create_temp_inbox() -> TempInbox:
    provider = settings.temp_email_provider.strip().lower()
    if provider in {"", "none", "disabled"}:
        raise TempEmailError("temporary email provider is disabled")
    if provider != "mail_tm":
        raise TempEmailError(
            f"unsupported temporary email provider: {settings.temp_email_provider}"
        )
    return await _create_mail_tm_inbox()


async def wait_for_code(inbox: TempInbox, *, timeout_seconds: int | None = None) -> dict[str, Any]:
    if inbox.provider != "mail_tm":
        raise TempEmailError(f"unsupported temporary email provider: {inbox.provider}")
    return await _wait_for_mail_tm_code(
        inbox,
        timeout_seconds=timeout_seconds or settings.temp_email_code_timeout_seconds,
    )


async def _create_mail_tm_inbox() -> TempInbox:
    base_url = settings.temp_email_base_url.rstrip("/")
    password = "Michelle-" + secrets.token_urlsafe(18)
    async with httpx.AsyncClient(base_url=base_url, timeout=20) as client:
        domains_resp = await client.get("/domains")
        domains_resp.raise_for_status()
        domains = domains_resp.json().get("hydra:member") or []
        domain = ""
        for item in domains:
            if isinstance(item, dict) and item.get("domain"):
                domain = str(item["domain"])
                break
        if not domain:
            raise TempEmailError("temporary email provider returned no domains")

        address = f"michelle-{secrets.token_hex(8)}@{domain}"
        account_resp = await client.post(
            "/accounts", json={"address": address, "password": password}
        )
        account_resp.raise_for_status()
        token_resp = await client.post("/token", json={"address": address, "password": password})
        token_resp.raise_for_status()
        token = str(token_resp.json().get("token") or "")
        if not token:
            raise TempEmailError("temporary email provider returned no token")

    return TempInbox(
        inbox_id=secrets.token_hex(8),
        address=address,
        password=password,
        token=token,
        provider="mail_tm",
    )


async def _wait_for_mail_tm_code(inbox: TempInbox, *, timeout_seconds: int) -> dict[str, Any]:
    base_url = settings.temp_email_base_url.rstrip("/")
    deadline = time.monotonic() + max(1, timeout_seconds)
    headers = {"Authorization": f"Bearer {inbox.token}"}
    last_subject = ""
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=20) as client:
        while time.monotonic() < deadline:
            messages_resp = await client.get("/messages")
            messages_resp.raise_for_status()
            messages = messages_resp.json().get("hydra:member") or []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                last_subject = str(message.get("subject") or last_subject)
                if not message_id:
                    continue
                detail_resp = await client.get(f"/messages/{message_id}")
                detail_resp.raise_for_status()
                detail = detail_resp.json()
                body = "\n".join(
                    str(detail.get(key) or "") for key in ("subject", "intro", "text", "html")
                )
                if match := CODE_RE.search(body):
                    return {
                        "email_address": inbox.address,
                        "code": match.group(1),
                        "subject": str(detail.get("subject") or last_subject),
                    }
            await _sleep(settings.temp_email_poll_interval_seconds)
    raise TempEmailError(
        f"verification code not received for {inbox.address} within {timeout_seconds}s"
        + (f"; last subject: {last_subject}" if last_subject else "")
    )


async def _sleep(seconds: int) -> None:
    import asyncio

    await asyncio.sleep(max(1, seconds))
