from __future__ import annotations

import html
import logging
from email.message import EmailMessage

import aiosmtplib
from sqlalchemy import func, select

from app.config import get_settings as get_app_settings
from app.crypto import decrypt
from app.db import SessionLocal
from app.models import Setting, Task, TaskResult

log = logging.getLogger("notify.email")

_KEYS = (
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "smtp_from",
    "smtp_starttls",
    "default_notify_email",
)


async def _load_settings() -> dict[str, str | None]:
    async with SessionLocal() as s:
        rows = (await s.execute(select(Setting).where(Setting.key.in_(_KEYS)))).scalars().all()
    out: dict[str, str | None] = {k: None for k in _KEYS}
    for r in rows:
        if r.value is None:
            continue
        out[r.key] = decrypt(r.value) if r.encrypted else r.value
    return out


async def send_completion(task_id: int) -> None:
    async with SessionLocal() as s:
        task = await s.get(Task, task_id)
        if task is None:
            return
        # per-engine counts
        rows = (
            await s.execute(
                select(TaskResult.engine, func.count(TaskResult.id))
                .where(TaskResult.task_id == task_id)
                .group_by(TaskResult.engine)
            )
        ).all()
    counts = {eng: int(n) for eng, n in rows}

    settings = await _load_settings()
    to_addr = task.notify_email or settings.get("default_notify_email")
    if not to_addr:
        log.info("task %s completed, no notification email set", task_id)
        return
    smtp_host = settings.get("smtp_host")
    smtp_from = settings.get("smtp_from") or settings.get("smtp_username")
    if not smtp_host or not smtp_from:
        log.warning("task %s completed but SMTP not configured", task_id)
        return

    base_url = get_app_settings().public_base_url.rstrip("/")
    link = f"{base_url}/#/history?task={task.id}"

    status_label = task.status.upper()
    counts_text = ", ".join(f"{eng}: {n}" for eng, n in sorted(counts.items())) or "no results"
    counts_html = (
        "<ul>"
        + "".join(
            f"<li><b>{html.escape(eng)}</b>: {n}</li>" for eng, n in sorted(counts.items())
        )
        + "</ul>"
    ) if counts else "<p><i>No results stored.</i></p>"

    msg = EmailMessage()
    msg["Subject"] = f"[SERP] {status_label}: {task.name}"
    msg["From"] = smtp_from
    msg["To"] = to_addr
    msg.set_content(
        f"Task '{task.name}' finished with status {status_label}.\n"
        f"Keywords: {', '.join(task.keywords)}\n"
        f"Engines: {', '.join(task.engines)}\n"
        f"Country: {task.country}\n"
        f"Results by engine: {counts_text}\n"
        f"{f'Error: {task.error_message}' if task.error_message else ''}\n"
        f"View: {link}\n"
    )
    msg.add_alternative(
        f"""<html><body style="font-family:system-ui,sans-serif">
        <h2>Task: {html.escape(task.name)}</h2>
        <p>Status: <b>{status_label}</b></p>
        <p>Keywords: {html.escape(', '.join(task.keywords))}</p>
        <p>Engines: {html.escape(', '.join(task.engines))} &middot; Country: {html.escape(task.country)}</p>
        {counts_html}
        {f'<p style="color:#c00">Error: {html.escape(task.error_message or "")}</p>' if task.error_message else ''}
        <p><a href="{link}">Open in SERP scraper</a></p>
        </body></html>""",
        subtype="html",
    )

    port = int(settings.get("smtp_port") or 587)
    starttls_pref = (settings.get("smtp_starttls") or "true").lower() != "false"
    user = settings.get("smtp_username")
    password = settings.get("smtp_password")

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=port,
            start_tls=starttls_pref and port != 465,
            use_tls=port == 465,
            username=user or None,
            password=password or None,
            timeout=30,
        )
        log.info("notification sent for task %s to %s", task_id, to_addr)
    except Exception:
        log.exception("smtp send failed for task %s", task_id)
