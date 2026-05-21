"""Send the merged report PDF via SMTP."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)


class EmailError(RuntimeError):
    pass


def _env(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None or v == "":
        raise EmailError(f"{name} env var is required")
    return v


def send_report(
    pdf_path: Path,
    *,
    subject: str,
    body: str,
    to_addr: str | None = None,
    from_addr: str | None = None,
) -> None:
    if not pdf_path.exists():
        raise EmailError(f"attachment not found: {pdf_path}")

    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587"))
    user = _env("SMTP_USER")
    password = _env("SMTP_PASS")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() != "false"
    sender = from_addr or _env("EMAIL_FROM")
    recipient = to_addr or _env("EMAIL_TO")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    data = pdf_path.read_bytes()
    msg.add_attachment(data, maintype="application", subtype="pdf", filename=pdf_path.name)

    log.info("emailing %s (%d bytes) to %s via %s:%d", pdf_path.name, len(data), recipient, host, port)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.ehlo()
            if use_tls:
                s.starttls(context=ctx)
                s.ehlo()
            s.login(user, password)
            s.send_message(msg)
