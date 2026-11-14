"""Branded email for password resets.

Off unless SMTP settings are in the environment. When off, "Forgot password?" requests
show up on the Admin page, and the Program Lead sends a reset link on Slack instead.

    SMTP_HOST=smtp.gmail.com   SMTP_PORT=587
    SMTP_USER=...              SMTP_PASSWORD=...   (an app password, not your real one)
    SMTP_FROM="Intern Summit Hub <you@example.com>"

Every message is multipart: a plain-text version and a branded HTML version. The HTML is
built to be recognizable rather than phish-like:
  - Summit header and colors (the same branding interns see in the app)
  - addressed by first name, and says which email address it was sent to
  - names the Program Lead, and shows the sign-in address as plain visible text
  - says plainly what to do if you didn't ask for it, and that we never ask for a reply
    containing your password

Logo: if frontend/img/css-logo.png exists, it's embedded in the email (shows in every
client without "display images"). Otherwise the email loads it from the Summit's site,
and the header still reads "CYBER SECURITY SUMMIT" in text for clients that block images.
"""

from __future__ import annotations

import html
import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import config

log = logging.getLogger("intern_hub.mailer")

LOGO_FILE = config.FRONTEND_DIR / "img" / "css-logo.png"
LOGO_URL = ("https://www.cybersecuritysummit.org/wp-content/uploads/2024/05/"
            "CS_SUMMIT_Logotag_reverse_hi-res-768x203.png")

NAVY, NAVY_DEEP, NEON, TEAL, INK, MUTED, LINE = (
    "#102c49", "#0a1d31", "#21fefe", "#04698d", "#14213d", "#5c6b80", "#e2e8f0")


def enabled() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def build_temp_password_email(to: str, first_name: str, temp_password: str, hours: int,
                              site_url: str) -> EmailMessage:
    when = datetime.now().strftime("%b %d, %Y at %I:%M %p").replace(" 0", " ")
    chair = f"{config.ADMIN_NAME}, {config.ADMIN_TITLE}"
    text = (
        f"Hi {first_name},\n\n"
        f"We received a request on {when} (Central) to reset the password for your Cyber Security "
        f"Summit Intern Summit Hub account ({to}).\n\n"
        f"Your temporary password is:\n\n    {temp_password}\n\n"
        f"To finish:\n"
        f"  1. Go to {site_url}\n"
        f"  2. Sign in with your email and the temporary password above\n"
        f"  3. Choose a new password (at least 12 characters)\n\n"
        f"The temporary password works once and expires in {hours} hours.\n\n"
        f"Didn't ask for this? You can ignore this email. Your current password still works and "
        f"nothing has changed. We will never ask you to reply with your password.\n\n"
        f"Questions? Message the Program Lead, {chair}, on Slack.\n\n"
        f"Cyber Security Summit Intern Program, Minneapolis\n"
    )
    e = html.escape
    code = e(temp_password)
    body = f"""
<p style="margin:0 0 14px;font-size:16px;">Hi {e(first_name)},</p>
<p style="margin:0 0 18px;">We received a request on <strong>{e(when)}</strong> (Central) to reset the
password for your Intern Summit Hub account <strong>{e(to)}</strong>. Here is your
<strong>temporary password</strong>:</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;">
  <tr><td align="center" style="background:#f0f9fc;border:1px dashed #9fd6e6;border-radius:12px;padding:18px 12px;">
    <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{MUTED};margin-bottom:6px;">Temporary password</div>
    <div style="font-family:'SFMono-Regular',Menlo,Consolas,monospace;font-size:24px;font-weight:700;letter-spacing:.08em;color:{NAVY};">{code}</div>
    <div style="font-size:12px;color:{MUTED};margin-top:6px;">Works once &middot; expires in {hours} hours</div>
  </td></tr>
</table>
<p style="margin:0 0 8px;font-weight:700;">To finish:</p>
<ol style="margin:0 0 20px;padding-left:20px;">
  <li style="margin:0 0 4px;">Go to the Intern Summit Hub sign-in page</li>
  <li style="margin:0 0 4px;">Sign in with your email and the temporary password above</li>
  <li style="margin:0;">Choose a new password (at least 12 characters)</li>
</ol>
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 8px;">
  <tr><td style="background:{TEAL};border-radius:10px;">
    <a href="{e(site_url)}" style="display:inline-block;padding:12px 22px;color:#ffffff;font-weight:700;text-decoration:none;font-size:15px;">Sign in to the Intern Summit Hub</a>
  </td></tr>
</table>
<p style="margin:0 0 22px;font-size:13px;color:{MUTED};">Or copy this address into your browser: <span style="color:{INK};">{e(site_url)}</span></p>
{_security_note(chair)}
"""
    return _message(to, "Your temporary Intern Summit Hub password", text, body,
                    preheader=f"Your one-time temporary password for the Intern Summit Hub. Expires in {hours} hours.")


def build_reset_link_email(to: str, first_name: str, link: str, hours: int) -> EmailMessage:
    chair = f"{config.ADMIN_NAME}, {config.ADMIN_TITLE}"
    text = (
        f"Hi {first_name},\n\n"
        f"Open this link to choose a new password for your Intern Summit Hub "
        f"account ({to}). It works once and expires in {hours} hours:\n\n{link}\n\n"
        f"Didn't ask for this? Ignore this email. Your password hasn't changed.\n\n"
        f"Questions? Message the Program Lead, {chair}, on Slack.\n"
    )
    e = html.escape
    body = f"""
<p style="margin:0 0 14px;font-size:16px;">Hi {e(first_name)},</p>
<p style="margin:0 0 18px;">Use the button below to choose a new password for your Intern Summit Hub account
<strong>{e(to)}</strong>. The link works once and expires in {hours} hours.</p>
<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 8px;">
  <tr><td style="background:{TEAL};border-radius:10px;">
    <a href="{e(link)}" style="display:inline-block;padding:12px 22px;color:#ffffff;font-weight:700;text-decoration:none;font-size:15px;">Choose a new password</a>
  </td></tr>
</table>
<p style="margin:0 0 22px;font-size:13px;color:{MUTED};word-break:break-all;">Link: <span style="color:{INK};">{e(link)}</span></p>
{_security_note(chair)}
"""
    return _message(to, "Reset your Intern Summit Hub password", text, body,
                    preheader="Your one-time link to choose a new Intern Summit Hub password.")


def send_temp_password_email(to: str, first_name: str, temp_password: str, hours: int,
                             site_url: str) -> bool:
    return _send(build_temp_password_email(to, first_name, temp_password, hours, site_url))


def send_reset_email(to: str, first_name: str, link: str, hours: int) -> bool:
    return _send(build_reset_link_email(to, first_name, link, hours))


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _security_note(chair: str) -> str:
    e = html.escape
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
  <tr><td style="background:#f6f8fb;border-left:4px solid {NAVY};border-radius:8px;padding:14px 16px;font-size:13px;line-height:1.55;color:{INK};">
    <strong>Didn't ask for this?</strong> You can ignore this email. Your current password still works
    and nothing has changed.<br>
    <strong>Staying safe:</strong> we will never ask you to reply with your password or send it
    anywhere else. Questions? Message the Program Lead, <strong>{e(chair)}</strong>, on Slack.
  </td></tr>
</table>"""


def _logo_src() -> tuple[str, bool]:
    if LOGO_FILE.is_file():
        return "cid:css-logo", True
    return LOGO_URL, False


def _wrap(inner: str, preheader: str, to: str) -> str:
    e = html.escape
    logo_src, _ = _logo_src()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="light"><title>Intern Summit Hub</title></head>
<body style="margin:0;padding:0;background:#eef2f7;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f7;">
<tr><td align="center" style="padding:28px 12px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:14px;overflow:hidden;font-family:'Open Sans',Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;color:{INK};">
    <tr><td style="background:{NAVY};background-image:linear-gradient(135deg,{NAVY},{NAVY_DEEP});padding:22px 28px;border-bottom:3px solid {NEON};">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <img src="{logo_src}" width="200" alt="CYBER SECURITY SUMMIT" style="display:block;width:200px;max-width:100%;height:auto;border:0;color:#ffffff;font-family:Montserrat,Arial,sans-serif;font-size:18px;font-weight:800;letter-spacing:.04em;">
        </td>
        <td valign="middle" align="right">
          <span style="display:inline-block;background:{NEON};color:{NAVY};font-family:Montserrat,Arial,sans-serif;font-weight:800;font-size:12px;letter-spacing:.1em;text-transform:uppercase;padding:5px 10px;border-radius:6px;">Intern Summit Hub</span>
        </td>
      </tr></table>
    </td></tr>
    <tr><td style="padding:28px 28px 26px;">{inner}</td></tr>
    <tr><td style="background:#f6f8fb;border-top:1px solid {LINE};padding:16px 28px;font-size:12px;line-height:1.5;color:{MUTED};">
      Cyber Security Summit Intern Program &middot; Minneapolis, MN<br>
      This message was sent to {e(to)} because a password reset was requested for that Intern Summit Hub account.
    </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""


def _message(to: str, subject: str, text: str, inner_html: str, preheader: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM", "Intern Summit Hub <noreply@localhost>")
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    sender_domain = msg["From"].addresses[0].domain or "localhost"
    msg["Message-ID"] = make_msgid(domain=sender_domain)
    msg.set_content(text)
    msg.add_alternative(_wrap(inner_html, preheader, to), subtype="html")
    _, embedded = _logo_src()
    if embedded:
        msg.get_payload()[1].add_related(LOGO_FILE.read_bytes(), maintype="image", subtype="png",
                                         cid="<css-logo>", filename="css-logo.png")
    return msg


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _send(msg: EmailMessage) -> bool:
    if not enabled():
        return False
    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")),
                          timeout=15) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                # Google shows app passwords as "abcd efgh ijkl mnop"; the spaces aren't part of it.
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", "").replace(" ", ""))
            s.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        log.exception("reset email failed")
        return False
