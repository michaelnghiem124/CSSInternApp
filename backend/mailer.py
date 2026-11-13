"""Optional email for password-reset links.

Off unless SMTP settings are in the environment. When off, "Forgot password?" requests
show up on the Admin page, and the Program Chair sends the reset link on Slack instead.

    SMTP_HOST=smtp.gmail.com   SMTP_PORT=587
    SMTP_USER=...              SMTP_PASSWORD=...   (an app password, not your real one)
    SMTP_FROM="CSS Intern Hub <you@example.com>"
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("intern_hub.mailer")


def enabled() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_temp_password_email(to: str, first_name: str, temp_password: str, hours: int,
                             site_url: str) -> bool:
    body = (
        f"Hi {first_name},\n\n"
        f"Someone asked to reset the password for your Cyber Security Summit Intern Hub account.\n\n"
        f"Your temporary password is:\n\n    {temp_password}\n\n"
        f"Sign in at {site_url} with your email and this temporary password. You'll be asked to "
        f"choose a new password right away. It works once and expires in {hours} hours.\n\n"
        f"If you didn't ask for this, ignore this email. Your current password still works.\n"
    )
    return _send(to, "Your temporary CSS Intern Hub password", body)


def send_reset_email(to: str, first_name: str, link: str, hours: int) -> bool:
    body = (
        f"Hi {first_name},\n\n"
        f"Someone asked to reset the password for your Cyber Security Summit Intern Hub account. "
        f"Open this link to choose a new one (it works once and expires in {hours} hours):\n\n"
        f"{link}\n\n"
        f"If you didn't ask for this, you can ignore this email; your password hasn't changed.\n"
    )
    return _send(to, "Reset your CSS Intern Hub password", body)


def _send(to: str, subject: str, body: str) -> bool:
    if not enabled():
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")),
                          timeout=15) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        log.exception("reset email failed")
        return False
