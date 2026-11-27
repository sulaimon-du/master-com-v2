"""aiogram glue around the sync dialog (spec 2026-11-22, D5, D8). Runs only in the bot process.

Polling when `TELEGRAM_WEBHOOK_URL` is unset (development), webhook otherwise (staging on Render):
an aiohttp server on `0.0.0.0:$PORT` with Telegram's updates at `/telegram/webhook` and
`GET /health` for Render. Logs carry update types and outcomes only: never tokens, phone numbers
or the bot token.

With `RUN_WORKER=1` (staging, spec D7) the job worker runs in a supervised daemon thread of this
process; `GET /health` doubles as the site's wake-up ping and makes it poll at once.
"""

import asyncio
import hmac
import logging
import os
import re
import traceback
from collections.abc import Callable

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.bot import dialog
from app.bot.dialog import Reply, TgUser
from app.core.config import get_settings
from app.core.i18n import translate
from app.core.monitoring import init_sentry
from app.worker.background import BackgroundWorker

logger = logging.getLogger("app.bot")

WEBHOOK_PATH = "/telegram/webhook"

Markup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove


# Telegram's rule for setWebhook secret_token.
SECRET_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")


def clean_setting(value: str | None) -> str:
    """Strip the spaces and line breaks a value often picks up when pasted into a dashboard."""
    return (value or "").strip()


def check_webhook_secret(header: str | None, expected: str) -> bool:
    """Constant-time check of Telegram's secret header. An unset secret refuses everything."""
    if not header or not expected:
        return False
    return hmac.compare_digest(header.encode(), expected.encode())


def _inline(reply: Reply) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(text=b.text, url=b.url)
        if b.url
        else InlineKeyboardButton(text=b.text, callback_data=b.callback)
        for b in reply.buttons
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def to_messages(reply: Reply) -> list[tuple[str, Markup | None]]:
    """Turn a dialog reply into the messages to send, each with at most one keyboard.

    Telegram takes one keyboard per message, so a reply that both removes the contact keyboard
    and has inline buttons becomes two messages; the second is titled with the first button.
    """
    if reply.ask_contact:
        button = KeyboardButton(text=translate(reply.lang, "bot.share_phone"), request_contact=True)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True
        )
        return [(reply.text, keyboard)]
    if reply.remove_keyboard and reply.buttons:
        return [
            (reply.text, ReplyKeyboardRemove()),
            (reply.buttons[0].text, _inline(reply)),
        ]
    if reply.remove_keyboard:
        return [(reply.text, ReplyKeyboardRemove())]
    if reply.buttons:
        return [(reply.text, _inline(reply))]
    return [(reply.text, None)]


async def _send(bot: Bot, chat_id: int, reply: Reply) -> None:
    for text, markup in to_messages(reply):
        await bot.send_message(chat_id, text, reply_markup=markup)


def _tg_user(message_or_query: Message | CallbackQuery) -> TgUser | None:
    user = message_or_query.from_user
    if user is None or user.is_bot:
        return None
    return TgUser(id=user.id, first_name=user.first_name, language_code=user.language_code)


def _start_kind(payload: str) -> str:
    # Never log the payload itself: link payloads carry a one-time token.
    if payload in ("login", "phone"):
        return payload
    return "link" if payload.startswith("link_") else "other"


async def on_start(message: Message, command: CommandObject, bot: Bot) -> None:
    user = _tg_user(message)
    if user is None:
        return
    payload = command.args or ""
    reply = await asyncio.to_thread(dialog.on_start, user, payload)
    await _send(bot, message.chat.id, reply)
    logger.info("bot update=start kind=%s outcome=replied", _start_kind(payload))


async def on_phone(message: Message, bot: Bot) -> None:
    user = _tg_user(message)
    if user is None:
        return
    reply = await asyncio.to_thread(dialog.on_start, user, "phone")
    await _send(bot, message.chat.id, reply)
    logger.info("bot update=phone_command outcome=replied")


async def on_contact(message: Message, bot: Bot) -> None:
    user = _tg_user(message)
    if user is None or message.contact is None:
        return
    contact = message.contact
    reply = await asyncio.to_thread(dialog.on_contact, user, contact.user_id, contact.phone_number)
    await _send(bot, message.chat.id, reply)
    own = contact.user_id == user.id
    logger.info("bot update=contact own=%s outcome=replied", own)


async def on_text(message: Message, bot: Bot) -> None:
    user = _tg_user(message)
    if user is None:
        return
    reply = await asyncio.to_thread(dialog.on_text, user)
    await _send(bot, message.chat.id, reply)
    logger.info("bot update=message outcome=help")


async def _remove_buttons(query: CallbackQuery) -> None:
    """Take the Yes/Cancel buttons off the message so a second tap is impossible."""
    if not isinstance(query.message, Message):
        return
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception as exc:  # never mask the reply; the message may be too old to edit
        logger.warning("bot could not remove buttons: %s", type(exc).__name__)


async def on_callback(query: CallbackQuery, bot: Bot) -> None:
    user = _tg_user(query)
    try:
        if user is None:
            return
        reply = await asyncio.to_thread(dialog.on_callback, user, query.data or "")
    finally:
        # Always answered, even when the dialog fails, so the button stops spinning.
        await query.answer()
    await _remove_buttons(query)
    await _send(bot, user.id, reply)
    kind = "cancel" if query.data == dialog.CANCEL else "confirm"
    logger.info("bot update=callback kind=%s outcome=replied", kind)


async def on_error(event: ErrorEvent) -> bool:
    # Exception messages may quote data (a database error can echo a phone number), so only
    # the exception type and the stack frames are logged, never the message.
    exc = event.exception
    frames = "".join(traceback.format_tb(exc.__traceback__))
    logger.error("bot update failed: %s\n%s", type(exc).__name__, frames)
    return True


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    # Private chats only: a sign-in link posted into a group would be readable by everyone.
    dp.message.filter(F.chat.type == ChatType.PRIVATE)
    dp.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    dp.message.register(on_start, CommandStart())
    dp.message.register(on_phone, Command("phone"))
    dp.message.register(on_contact, F.contact)
    dp.message.register(on_text)
    dp.callback_query.register(on_callback)
    dp.errors.register(on_error)
    return dp


class _SecretCheckedHandler(SimpleRequestHandler):
    def verify_secret(self, telegram_secret_token: str, bot: Bot) -> bool:
        return check_webhook_secret(telegram_secret_token, self.secret_token or "")


def build_webhook_app(
    bot: Bot, dp: Dispatcher, secret: str, *, on_health: Callable[[], None] | None = None
) -> web.Application:
    """`on_health` runs on every `GET /health` (the worker's wake-up); it never fails the check."""

    async def health(_request: web.Request) -> web.Response:
        if on_health is not None:
            try:
                on_health()
            except Exception as exc:  # the bot stays healthy whatever the worker does
                logger.error("worker wake-up failed: %s", type(exc).__name__)
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_get("/health", health)
    _SecretCheckedHandler(dispatcher=dp, bot=bot, secret_token=secret).register(
        app, path=WEBHOOK_PATH
    )
    setup_application(app, dp, bot=bot)
    return app


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_sentry(settings, component="bot")
    token = clean_setting(
        settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else None
    )
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set; the bot cannot start.")
    webhook_url = settings.telegram_webhook_url or ""
    secret = clean_setting(
        settings.telegram_webhook_secret.get_secret_value()
        if settings.telegram_webhook_secret
        else None
    )
    if not webhook_url and settings.is_production_like:
        # Polling would make Telegram drop this bot's webhook (D8: one bot per environment).
        raise SystemExit(
            f"Refusing polling with APP_ENV={settings.app_env}: set TELEGRAM_WEBHOOK_URL, "
            "or use the development bot's token locally."
        )
    if webhook_url and not secret:
        raise SystemExit("TELEGRAM_WEBHOOK_SECRET is required in webhook mode.")
    if webhook_url and not SECRET_TOKEN_RE.fullmatch(secret):
        # Telegram's own error ("illegal characters") is easy to misread; never echo the value.
        raise SystemExit(
            "TELEGRAM_WEBHOOK_SECRET may contain only letters, digits, _ and - "
            "(1-256 characters). Generate one with: "
            'python3 -c "import secrets; print(secrets.token_urlsafe(32))"'
        )

    dp = build_dispatcher()
    bot = Bot(token)
    worker = BackgroundWorker() if settings.run_worker else None
    if worker is not None:
        worker.start()
        logger.info("bot starting the worker thread")
    try:
        _serve(dp, bot, webhook_url, secret, worker)
    finally:
        if worker is not None:
            worker.shutdown()


def _serve(
    dp: Dispatcher, bot: Bot, webhook_url: str, secret: str, worker: BackgroundWorker | None
) -> None:
    if not webhook_url:
        logger.info("bot starting: polling")
        asyncio.run(dp.start_polling(bot))
        return

    async def set_webhook(bot: Bot) -> None:
        await bot.set_webhook(
            webhook_url, secret_token=secret, allowed_updates=dp.resolve_used_update_types()
        )
        logger.info("bot webhook set")

    dp.startup.register(set_webhook)
    port = int(os.environ.get("PORT", "8000"))
    logger.info("bot starting: webhook on port %s", port)
    # Render routes outside traffic to the container; it must listen on every interface.
    web.run_app(
        build_webhook_app(bot, dp, secret, on_health=worker.wake if worker else None),
        host="0.0.0.0",  # noqa: S104
        port=port,
        print=None,
    )
