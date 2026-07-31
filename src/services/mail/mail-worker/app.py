"""mail-worker — mail.email_verification.requested → 인증 메일 전송."""

from __future__ import annotations

import smtplib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from email.message import EmailMessage

from domains.mail.events import (
    EmailVerificationFailedBody,
    EmailVerificationRequestedBody,
    EmailVerificationSentBody,
)
from packages.config.logs import get_logger
from packages.config.settings import env
from packages.contracts.event_bus.bodies import EventBody
from packages.runtime.app import App, EventContext

app = App("mail-worker")
LOGGER = get_logger(__name__)

SMTP_HOST_ENV = "SMTP_HOST"
SMTP_PORT_ENV = "SMTP_PORT"
SMTP_USERNAME_ENV = "SMTP_USERNAME"
SMTP_PASSWORD_ENV = "SMTP_PASSWORD"
SMTP_FROM_ENV = "SMTP_FROM"
SMTP_STARTTLS_ENV = "SMTP_STARTTLS"
MAIL_DELIVERY_MODE_ENV = "MAIL_DELIVERY_MODE"

SMTP_TIMEOUT_SECONDS_ENV = "SMTP_TIMEOUT_SECONDS"  # SMTP 연결/전송 타임아웃 초(기본 10)

DEFAULT_SMTP_PORT = "587"
DEFAULT_SMTP_TIMEOUT_SECONDS = "10"
SMTP_MODE = "smtp"
LOG_MODE = "log"
SMTP_CONFIG_MISSING_REASON = "SMTP_HOST is required when MAIL_DELIVERY_MODE=smtp"
SMTP_CONFIG_MISSING_CODE = "smtp_config_missing"
SMTP_FROM_MISSING_REASON = "SMTP_FROM is required when MAIL_DELIVERY_MODE=smtp"
SMTP_FROM_MISSING_CODE = "smtp_from_missing"


@dataclass(frozen=True)
class MailDeliveryResult:
    sent: bool
    mode: str
    reason: str = ""
    reason_code: str = ""


def build_email_message(evt: EmailVerificationRequestedBody, sender: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = evt.email
    message["Subject"] = "Verify your email"
    message.set_content(
        "\n".join(
            [
                "Verify your email address to finish creating your account.",
                "",
                evt.verification_url,
                "",
                f"This link expires in {evt.expires_in_seconds // 60} minutes.",
            ]
        )
    )
    return message


def send_email_verification(evt: EmailVerificationRequestedBody) -> MailDeliveryResult:
    mode = env(MAIL_DELIVERY_MODE_ENV, SMTP_MODE).strip().lower() or SMTP_MODE
    if mode == LOG_MODE:
        LOGGER.info(
            "email verification requested",
            extra={"context": {"email": evt.email, "mode": LOG_MODE}},
        )
        return MailDeliveryResult(sent=True, mode=LOG_MODE)

    host = env(SMTP_HOST_ENV, "")
    if not host:
        LOGGER.error(
            "email verification smtp config missing",
            extra={"context": {"email": evt.email, "mode": SMTP_MODE}},
        )
        return MailDeliveryResult(
            sent=False,
            mode=SMTP_MODE,
            reason=SMTP_CONFIG_MISSING_REASON,
            reason_code=SMTP_CONFIG_MISSING_CODE,
        )

    sender = env(SMTP_FROM_ENV, "").strip()
    if not sender:
        LOGGER.error(
            "email verification smtp sender missing",
            extra={"context": {"email": evt.email, "mode": SMTP_MODE}},
        )
        return MailDeliveryResult(
            sent=False,
            mode=SMTP_MODE,
            reason=SMTP_FROM_MISSING_REASON,
            reason_code=SMTP_FROM_MISSING_CODE,
        )

    message = build_email_message(evt, sender)
    port = int(env(SMTP_PORT_ENV, DEFAULT_SMTP_PORT))
    username = env(SMTP_USERNAME_ENV, "")
    password = env(SMTP_PASSWORD_ENV, "")
    timeout_seconds = int(env(SMTP_TIMEOUT_SECONDS_ENV, DEFAULT_SMTP_TIMEOUT_SECONDS))
    with smtplib.SMTP(host, port, timeout=timeout_seconds) as client:
        if env(SMTP_STARTTLS_ENV, "1") != "0":
            client.starttls()
        if username:
            client.login(username, password)
        client.send_message(message)
    return MailDeliveryResult(sent=True, mode=SMTP_MODE)


@app.on(EmailVerificationRequestedBody)
async def on_email_verification_requested(
    evt: EmailVerificationRequestedBody, ctx: EventContext[object]
) -> AsyncIterator[EventBody]:
    result = send_email_verification(evt)
    if result.sent:
        yield EmailVerificationSentBody(email=evt.email, mode=result.mode)
        return
    yield EmailVerificationFailedBody(
        email=evt.email,
        reason=result.reason,
        reason_code=result.reason_code,
        mode=result.mode,
    )


if __name__ == "__main__":
    app.run()
