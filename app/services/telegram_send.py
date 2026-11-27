"""Send one Telegram message with the Bot API `sendMessage` over stdlib HTTP (spec D8, D10.8).

Delivery semantics (Global Constraints): retry only when the message certainly was not delivered.
- the connection or sending failed (DNS, refused, connect timeout)  -> `TelegramUnavailable`
- HTTP 5xx                                                        -> `TelegramUnavailable`
- HTTP 429                                       -> `jobs.RetryAfter(parameters.retry_after)`
- no answer after the request was sent (read timeout)             -> `Delivery.SENT` (no retry)
- HTTP 403 (the user blocked the bot)                             -> `Delivery.BLOCKED`
- any other 4xx (chat not found, bad request)                     -> `Delivery.REJECTED`

The token is read from settings at send time and lives only on the bot service (R-021); with no
token the send fails closed (`TelegramNotConfigured`). The token is part of the request path,
so no exception raised here carries a message, a URL or a chained cause, and nothing here logs
the token, the chat id or the text.
"""

import enum
import http.client
import json
import logging
import ssl
from collections.abc import Callable
from typing import Any

from app.core.config import get_settings
from app.services.jobs import RetryAfter

log = logging.getLogger(__name__)

API_HOST = "api.telegram.org"
# Far below the worker's 10-minute stale lock: a send happens inside the job's transaction.
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0
# Telegram's own limit on a message text.
MAX_TEXT = 4096


class Delivery(enum.Enum):
    SENT = "sent"
    BLOCKED = "blocked"
    REJECTED = "rejected"


class TelegramNotConfigured(Exception):
    """TELEGRAM_BOT_TOKEN is not set where the worker runs."""


class TelegramUnavailable(Exception):
    """The message was certainly not delivered (no connection, or a 5xx): retry later."""


class NotSent(Exception):
    """Transport: the request never fully reached Telegram."""


class NoAnswer(Exception):
    """Transport: the request was sent, but no answer arrived in time."""


# (path, JSON body) -> (HTTP status, response body). Raises NotSent or NoAnswer only.
Transport = Callable[[str, bytes], tuple[int, bytes]]


def https_transport(path: str, body: bytes) -> tuple[int, bytes]:
    """POST `body` to api.telegram.org: connect within 5 s, answer within 10 s."""
    conn = http.client.HTTPSConnection(
        API_HOST, timeout=CONNECT_TIMEOUT, context=ssl.create_default_context()
    )
    try:
        try:
            conn.connect()
            if conn.sock is not None:
                conn.sock.settimeout(READ_TIMEOUT)
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        except Exception:
            # The original error is dropped on purpose: it may repeat the path (the token).
            raise NotSent from None
        try:
            response = conn.getresponse()
            return response.status, response.read()
        except Exception:
            raise NoAnswer from None
    finally:
        conn.close()


# Tests replace this; nothing else should.
transport: Transport = https_transport


def _retry_after(body: bytes) -> float:
    try:
        data: Any = json.loads(body)
        return float(data["parameters"]["retry_after"])
    except Exception:  # an odd 429 body: wait the usual backoff
        return 0.0


def send_message(chat_id: int, text: str) -> Delivery:
    """Send `text` (plain, no markup) to the private chat `chat_id`."""
    token = get_settings().telegram_bot_token
    secret = token.get_secret_value().strip() if token is not None else ""
    if not secret:
        log.error("telegram send refused: TELEGRAM_BOT_TOKEN is not set")
        raise TelegramNotConfigured
    body = json.dumps(
        {"chat_id": chat_id, "text": text[:MAX_TEXT], "disable_web_page_preview": True}
    ).encode()
    try:
        status, answer = transport(f"/bot{secret}/sendMessage", body)
    except NoAnswer:
        # It may well have arrived: a second copy is worse than a lost one (spec D10.8).
        log.warning("telegram send: no answer in time, treated as delivered")
        return Delivery.SENT
    except NotSent:
        raise TelegramUnavailable from None
    if 200 <= status < 300:
        return Delivery.SENT
    if status == 403:
        return Delivery.BLOCKED
    if status == 429:
        raise RetryAfter(_retry_after(answer)) from None
    if status >= 500:
        raise TelegramUnavailable from None
    log.warning("telegram send rejected with HTTP %d", status)
    return Delivery.REJECTED
