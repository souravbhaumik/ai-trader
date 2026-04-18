"""Simple SMTP email service.

Uses Python's built-in smtplib with STARTTLS (port 587) or SSL (port 465).
All settings come from environment variables via app.core.config.settings.
If SMTP_HOST is not configured the service silently skips sending (no crash).
"""
from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


def _send(*, to: str, subject: str, html: str, text: str) -> None:
    """Internal helper — opens SMTP connection, sends, closes."""
    if not settings.smtp_host:
        logger.warning("email.skipped", reason="SMTP_HOST not configured", to=to)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        if settings.smtp_port == 465:
            # SSL from the start
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ctx) as server:
                if settings.smtp_user and settings.smtp_password:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, to, msg.as_string())
        else:
            # STARTTLS (port 587 or custom)
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                if settings.smtp_user and settings.smtp_password:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, to, msg.as_string())
        logger.info("email.sent", to=to, subject=subject)
    except Exception as exc:
        logger.error("email.failed", to=to, subject=subject, error=str(exc))


def send_invite_email(*, to_email: str, registration_url: str, invited_by_name: str = "Admin") -> None:
    """Send an invite email with the registration link."""
    subject = "You're invited to AI Trader"
    text = (
        f"Hi,\n\n"
        f"{invited_by_name} has invited you to join AI Trader.\n\n"
        f"Click the link below to create your account (expires in 24 hours):\n"
        f"{registration_url}\n\n"
        f"If you didn't expect this invite, you can safely ignore this email.\n"
    )
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;background:#0f1117;color:#e2e8f0;padding:32px;">
  <div style="max-width:480px;margin:0 auto;background:#1a1f2e;border-radius:12px;padding:32px;border:1px solid #2d3748;">
    <h2 style="margin:0 0 8px;color:#ffffff;">You're invited to AI Trader</h2>
    <p style="color:#94a3b8;margin:0 0 24px;">{invited_by_name} has invited you to join the platform.</p>
    <a href="{registration_url}"
       style="display:inline-block;padding:12px 24px;background:#3b82f6;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:600;">
      Create Account
    </a>
    <p style="color:#64748b;font-size:12px;margin-top:24px;">
      This link expires in 24 hours. If you didn't expect this invite, ignore this email.
    </p>
    <p style="color:#475569;font-size:11px;margin-top:8px;word-break:break-all;">
      Or copy this link: {registration_url}
    </p>
  </div>
</body>
</html>
"""
    _send(to=to_email, subject=subject, html=html, text=text)
