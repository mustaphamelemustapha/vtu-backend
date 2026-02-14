from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Optional

import httpx

from app.core.config import get_settings


def _sanitize_email_from(value: str) -> str:
    # Render env vars are often pasted with surrounding quotes; Resend rejects that.
    v = (value or "").strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        v = v[1:-1].strip()
    return v


def _sanitize_email(value: str) -> str:
    return (value or "").strip()

def _parse_from(value: str) -> tuple[Optional[str], str]:
    """
    Accept either:
      - email@example.com
      - Name <email@example.com>
    Returns (name, email).
    """
    raw = _sanitize_email_from(value)
    name, email = parseaddr(raw)
    name = (name or "").strip() or None
    email = (email or "").strip()
    # If parseaddr fails (malformed input), fall back to the raw string.
    if not email:
        return None, raw.strip()
    return name, email


def _build_reset_email_html(reset_link: str) -> str:
    # Keep HTML minimal and compatible across mail clients.
    return f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #0f172a;">
      <h2 style="margin: 0 0 8px;">Reset your AxisVTU password</h2>
      <p style="margin: 0 0 14px;">
        We received a request to reset your password. If you did not request this, you can ignore this email.
      </p>
      <p style="margin: 0 0 16px;">
        <a href="{reset_link}" style="display:inline-block;background:#0f766e;color:#fff;padding:10px 14px;border-radius:12px;text-decoration:none;font-weight:700;">
          Reset Password
        </a>
      </p>
      <p style="margin: 0 0 6px; color: #475569; font-size: 13px;">
        Or copy and paste this link:
      </p>
      <p style="margin: 0; font-size: 13px;">
        <a href="{reset_link}" style="color:#0f766e;">{reset_link}</a>
      </p>
    </div>
    """.strip()


def send_password_reset_email(to_email: str, reset_token: str) -> None:
    settings = get_settings()
    reset_link = f"{settings.frontend_base_url.rstrip('/')}/app/?reset=1&token={reset_token}"
    subject = "Reset your AxisVTU password"
    html = _build_reset_email_html(reset_link)
    to_email = _sanitize_email(to_email)

    provider = (settings.email_provider or "console").lower()
    if provider == "console":
        # Safe default for dev/test; shows link in logs.
        print(f"[email][console] to={to_email} subject={subject} link={reset_link}")
        return

    if provider == "resend":
        _send_via_resend(
            api_key=settings.resend_api_key,
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "brevo":
        name, from_email = _parse_from(settings.email_from)
        _send_via_brevo(
            api_key=settings.brevo_api_key,
            from_name=name,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    if provider == "smtp":
        _send_via_smtp(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            email_from=_sanitize_email_from(settings.email_from),
            to_email=to_email,
            subject=subject,
            html=html,
        )
        return

    raise ValueError(f"Unsupported EMAIL_PROVIDER: {settings.email_provider}")


def _send_via_resend(
    *,
    api_key: Optional[str],
    email_from: str,
    to_email: str,
    subject: str,
    html: str,
) -> None:
    if not api_key:
        raise ValueError("RESEND_API_KEY is required when EMAIL_PROVIDER=resend")

    payload = {
        "from": email_from,
        "to": [to_email],
        "subject": subject,
        "html": html,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=15) as client:
        res = client.post("https://api.resend.com/emails", json=payload, headers=headers)
        if res.status_code >= 400:
            raise RuntimeError(f"Resend error: {res.status_code} {res.text}")

def _send_via_brevo(
    *,
    api_key: Optional[str],
    from_name: Optional[str],
    from_email: str,
    to_email: str,
    subject: str,
    html: str,
) -> None:
    if not api_key:
        raise ValueError("BREVO_API_KEY is required when EMAIL_PROVIDER=brevo")
    if not from_email:
        raise ValueError("EMAIL_FROM is required when EMAIL_PROVIDER=brevo")

    payload = {
        "sender": {"name": from_name or "AxisVTU", "email": from_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
    }
    headers = {"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"}
    with httpx.Client(timeout=15) as client:
        res = client.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers)
        if res.status_code >= 400:
            raise RuntimeError(f"Brevo error: {res.status_code} {res.text}")


def _send_via_smtp(
    *,
    host: Optional[str],
    port: int,
    username: Optional[str],
    password: Optional[str],
    use_tls: bool,
    email_from: str,
    to_email: str,
    subject: str,
    html: str,
) -> None:
    if not host:
        raise ValueError("SMTP_HOST is required when EMAIL_PROVIDER=smtp")

    msg = EmailMessage()
    msg["From"] = email_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content("Use an HTML-capable email client to view this message.")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port, timeout=15) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        if username and password:
            server.login(username, password)
        server.send_message(msg)
