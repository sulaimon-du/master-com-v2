"""Outgoing email behind one interface (PRODUCT_RULES A4: a real provider comes later).

Development and staging write each message to the log. Production refuses to send until a
provider is chosen, so nothing is silently dropped there.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


class LogEmailSender:
    """Writes the message to the log, so the owner can open the link during testing."""

    def send(self, message: OutgoingEmail) -> None:
        log.info("email to=%s subject=%r\n%s", message.to, message.subject, message.body)


def get_email_sender() -> EmailSender:
    if get_settings().app_env == "production":
        raise RuntimeError("No email provider is configured for production (PRODUCT_RULES A4).")
    return LogEmailSender()
