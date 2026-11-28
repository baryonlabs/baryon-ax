"""Invite email for signup.

SMTP settings are optional. With no ``SMTP_HOST`` (the development default) the
sign-in link is written to the log and signup still succeeds, so a local demo
works without a mail server. In production set the SMTP_* variables; a send
failure is logged and reported as ``email_sent: false`` rather than undoing the
organization that was just created.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

logger = logging.getLogger("baryon.ax.mail")


@dataclass(frozen=True)
class MailSettings:
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    sender: str = "no-reply@baryon.local"
    starttls: bool = True
    ssl: bool = False
    timeout: float = 10.0

    @classmethod
    def from_env(cls) -> "MailSettings":
        return cls(
            host=os.getenv("SMTP_HOST", "").strip(),
            port=int(os.getenv("SMTP_PORT", "587")),
            user=os.getenv("SMTP_USER", "").strip(),
            password=os.getenv("SMTP_PASS", ""),
            sender=os.getenv("SMTP_FROM", "no-reply@baryon.local").strip() or "no-reply@baryon.local",
            starttls=os.getenv("SMTP_STARTTLS", "true").strip().lower() not in {"0", "false", "no", "off"},
            ssl=os.getenv("SMTP_SSL", "false").strip().lower() in {"1", "true", "yes", "on"},
            timeout=float(os.getenv("SMTP_TIMEOUT_SECONDS", "10")),
        )


def invite_subject(organization_name: str) -> str:
    return f"[Baryon AX] {organization_name} 조직이 만들어졌습니다 / Your organization is ready"


def invite_message(to: str, organization_name: str, organization_id: str, login_url: str, sender: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = invite_subject(organization_name)
    message.set_content(
        f"""안녕하세요,

'{organization_name}' 조직이 Baryon AX에 만들어졌습니다.
아래 링크에서 로그인하고 초대를 수락하세요.

{login_url}

조직 ID: {organization_id}

--

Hello,

The organization "{organization_name}" has been created on Baryon AX.
Sign in and accept your invitation here:

{login_url}

Organization ID: {organization_id}
""",
        charset="utf-8",
    )
    return message


class Mailer:
    def __init__(self, settings: MailSettings | None = None) -> None:
        self.settings = settings or MailSettings.from_env()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.host)

    def send_invite(self, to: str, organization_name: str, organization_id: str, login_url: str) -> bool:
        """Send the sign-in mail. Returns False when mail is off or the send failed."""
        if not self.enabled:
            logger.info(
                "SMTP not configured; sign-in link for organization %s <%s>: %s",
                organization_id, to, login_url,
            )
            return False
        message = invite_message(to, organization_name, organization_id, login_url, self.settings.sender)
        try:
            if self.settings.ssl:
                with smtplib.SMTP_SSL(
                    self.settings.host, self.settings.port, timeout=self.settings.timeout,
                    context=ssl.create_default_context(),
                ) as server:
                    if self.settings.user:
                        server.login(self.settings.user, self.settings.password)
                    server.send_message(message)
            else:
                with smtplib.SMTP(self.settings.host, self.settings.port, timeout=self.settings.timeout) as server:
                    if self.settings.starttls:
                        server.starttls(context=ssl.create_default_context())
                    if self.settings.user:
                        server.login(self.settings.user, self.settings.password)
                    server.send_message(message)
            return True
        except Exception:
            logger.exception("Could not send the invite email to %s for organization %s", to, organization_id)
            return False
