"""E-mail via SMTP (STARTTLS by default)."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Callable, Sequence

from .base import Message, NotifyError

SmtpFactory = Callable[[str, int], smtplib.SMTP]


class EmailNotifier:
    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        sender: str,
        recipients: Sequence[str],
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        smtp_factory: SmtpFactory = smtplib.SMTP,
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.sender = sender
        self.recipients = [r.strip() for r in recipients if r.strip()]
        self._username = username
        self._password = password
        self._starttls = starttls
        self._smtp_factory = smtp_factory
        if not self.recipients:
            raise ValueError("email notifier needs at least one recipient")

    def build(self, message: Message) -> EmailMessage:
        mail = EmailMessage()
        mail["Subject"] = message.subject
        mail["From"] = self.sender
        mail["To"] = ", ".join(self.recipients)
        mail.set_content(message.body)
        return mail

    def send(self, message: Message) -> None:
        mail = self.build(message)
        try:
            with self._smtp_factory(self.host, self.port) as smtp:
                if self._starttls:
                    smtp.starttls()
                if self._username:
                    smtp.login(self._username, self._password or "")
                smtp.send_message(mail)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotifyError(f"smtp failed: {exc.__class__.__name__}: {exc}") from exc
