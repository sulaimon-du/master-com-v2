"""Outgoing email behind one interface (PRODUCT_RULES A4: a real provider comes later).

Development and staging write each message to the log. Production logs an error instead of
sending until a provider is chosen, so a missing provider is visible but never breaks a page.
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


class UnconfiguredEmailSender:
    """Production before a provider is chosen: the email is not sent and an error is logged.

    It does not raise, so pages answer the same as when an email goes out (a 500 here would
    reveal which accounts exist). The address and the link are not logged in production.
    """

    def send(self, message: OutgoingEmail) -> None:
        log.error("email not sent: no provider configured (PRODUCT_RULES A4)")


def get_email_sender() -> EmailSender:
    if get_settings().app_env == "production":
        return UnconfiguredEmailSender()
    return LogEmailSender()
