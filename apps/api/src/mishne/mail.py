"""Sending email, behind a provider interface.

Nothing in this system sent any until invitations needed to, and the shape is
the same one `auth/providers.py` and `billing/payments.py` already use: a
narrow protocol, a `console` implementation that is not a mock, and a real one
chosen by configuration.

`console` prints the message and, crucially, the link. That is not a stub of
sending — it is what a developer's machine should do, and it means the whole
invitation path is exercisable with no account anywhere and no risk of mailing
a real person from a test. The alternative, silently doing nothing, produces an
invitation flow that appears to work and delivers nothing.

## What is not in here

No templating engine and no HTML. An invitation is four lines and a link; a
system that renders it through a template language has a template language to
maintain, and the first thing that breaks in an email is the HTML. Plain text
arrives everywhere and cannot be mis-rendered.

No queue and no retry. A send that fails raises, and the caller decides — for
invitations the caller rolls the invitation back rather than leaving a row
nobody was told about.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from .config import Settings, get_settings
from .logging import get_logger

log = get_logger(__name__)


class MailError(RuntimeError):
    """The message could not be handed to anything that would deliver it."""


@dataclass
class Message:
    to: str
    subject: str
    body: str

    def as_email(self, sender: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = self.to
        message["Subject"] = self.subject
        message.set_content(self.body)
        return message


class Mailer(Protocol):
    name: str

    def send(self, message: Message) -> None:
        ...


@dataclass
class ConsoleMailer:
    """Prints it. What a developer's machine uses.

    The body goes to stdout in full, including the link, because the point of
    running this locally is to click it. The structured log line beside it
    carries the recipient and the subject and NOT the body: a link in an
    invitation is a credential, and logs outlive the process that wrote them.
    """

    name = "console"
    sender: str = "mishne.ai <no-reply@localhost>"

    def send(self, message: Message) -> None:
        print(
            f"\n─── email ───────────────────────────────────────────────\n"
            f"To:      {message.to}\n"
            f"Subject: {message.subject}\n\n"
            f"{message.body}\n"
            f"─────────────────────────────────────────────────────────\n",
            flush=True,
        )
        log.info("mail.sent", provider=self.name, to_domain=_domain(message.to),
                 subject=message.subject)


@dataclass
class SMTPMailer:
    """Anything that speaks SMTP, which includes SES.

    TLS is not optional and there is no flag to turn it off: the credential in
    an invitation link is in the body, and a message sent in the clear is a
    membership grant on the wire.
    """

    name = "smtp"
    settings: Settings

    def send(self, message: Message) -> None:
        cfg = self.settings
        if not cfg.smtp_host:
            raise MailError("MAIL_PROVIDER=smtp needs SMTP_HOST")
        email = message.as_email(cfg.mail_from)
        try:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as server:
                server.starttls(context=ssl.create_default_context())
                if cfg.smtp_user:
                    server.login(cfg.smtp_user, cfg.smtp_password)
                server.send_message(email)
        except Exception as exc:  # noqa: BLE001 — every SMTP failure is one failure
            # The type, not the message: an SMTP error quotes the envelope,
            # which contains the recipient's address.
            raise MailError(f"{type(exc).__name__}") from exc
        log.info("mail.sent", provider=self.name, to_domain=_domain(message.to),
                 subject=message.subject)


def _domain(address: str) -> str:
    """The domain only. An address is personal data and a log is not the place."""
    _, _, domain = address.partition("@")
    return domain or "?"


def get_mailer(settings: Settings | None = None) -> Mailer:
    settings = settings or get_settings()
    if settings.mail_provider == "smtp":
        return SMTPMailer(settings=settings)
    return ConsoleMailer(sender=settings.mail_from)
