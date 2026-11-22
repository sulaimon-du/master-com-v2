import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.bot import runner
from app.bot.dialog import Button, Reply
from app.core.config import get_settings
from app.core.i18n import translate

ROOT = Path(__file__).resolve().parent.parent
FAKE_TOKEN = "123456:TEST-token-not-real"


@pytest.fixture
def bot_env(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "MasterComTestBot")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_webhook_secret_must_match() -> None:
    assert runner.check_webhook_secret("s3cret-value", "s3cret-value") is True
    assert runner.check_webhook_secret("wrong", "s3cret-value") is False
    assert runner.check_webhook_secret(None, "s3cret-value") is False
    assert runner.check_webhook_secret("", "s3cret-value") is False
    # An unset secret never lets anything in.
    assert runner.check_webhook_secret("", "") is False
    # Non-ASCII junk in the header is a refusal, not a crash.
    assert runner.check_webhook_secret("секрет", "s3cret-value") is False


def _no_network(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise AssertionError("main() must stop before creating a Bot")


@pytest.mark.parametrize("env", ["staging", "production"])
def test_polling_is_refused_on_staging(bot_env, env: str) -> None:
    bot_env.setenv("APP_ENV", env)
    bot_env.setenv("TELEGRAM_WEBHOOK_URL", "")
    bot_env.setattr(runner, "Bot", _no_network)
    get_settings.cache_clear()
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert "polling" in str(exc.value.code)


def test_webhook_needs_a_secret(bot_env) -> None:
    bot_env.setenv("APP_ENV", "staging")
    bot_env.setenv("TELEGRAM_WEBHOOK_URL", "https://bot.example.com/telegram/webhook")
    bot_env.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    bot_env.setattr(runner, "Bot", _no_network)
    get_settings.cache_clear()
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert "TELEGRAM_WEBHOOK_SECRET" in str(exc.value.code)


def test_main_needs_a_token(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(runner, "Bot", _no_network)
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exc:
            runner.main()
    finally:
        get_settings.cache_clear()
    assert "TELEGRAM_BOT_TOKEN" in str(exc.value.code)


def test_web_app_does_not_import_aiogram() -> None:
    code = "import app.main, sys; app.main.create_app(); print('aiogram' in sys.modules)"
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "False"


def test_dispatcher_builds() -> None:
    dp = runner.build_dispatcher()
    # /start, /phone, contact, any other message.
    assert len(dp.message.handlers) == 4
    assert len(dp.callback_query.handlers) == 1


def test_reply_with_url_and_callback_buttons_is_inline() -> None:
    reply = Reply(
        "hi", buttons=(Button("Open", url="https://x.test/a"), Button("No", callback="x"))
    )
    [(text, markup)] = runner.to_messages(reply)
    assert text == "hi"
    row = markup.inline_keyboard[0]
    assert row[0].url == "https://x.test/a"
    assert row[1].callback_data == "x"


@pytest.mark.parametrize("lang", ["ru", "tg"])
def test_ask_contact_is_a_one_time_contact_keyboard(lang: str) -> None:
    [(_, markup)] = runner.to_messages(Reply("phone?", ask_contact=True, lang=lang))
    [[button]] = markup.keyboard
    assert button.request_contact is True
    assert button.text == translate(lang, "bot.share_phone")
    assert markup.one_time_keyboard is True


def test_remove_keyboard_and_buttons_become_two_messages() -> None:
    reply = Reply("ok", buttons=(Button("Open", url="https://x.test/a"),), remove_keyboard=True)
    first, second = runner.to_messages(reply)
    assert first[0] == "ok"
    assert first[1].remove_keyboard is True
    assert second[1].inline_keyboard[0][0].url == "https://x.test/a"


def test_plain_reply_has_no_markup() -> None:
    assert runner.to_messages(Reply("help")) == [("help", None)]


def test_webhook_app_checks_secret_and_has_health(bot_env) -> None:
    from aiogram import Bot
    from aiohttp.test_utils import TestClient, TestServer

    async def scenario() -> tuple[int, int, int]:
        bot = Bot(FAKE_TOKEN)
        app = runner.build_webhook_app(bot, runner.build_dispatcher(), "s3cret-value")
        async with TestClient(TestServer(app)) as client:
            health = await client.get("/health")
            missing = await client.post(runner.WEBHOOK_PATH, json={"update_id": 1})
            wrong = await client.post(
                runner.WEBHOOK_PATH,
                json={"update_id": 1},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )
            return health.status, missing.status, wrong.status

    assert asyncio.run(scenario()) == (200, 401, 401)


def _update(chat_type: str, *, callback: bool) -> "object":
    from datetime import UTC, datetime

    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    user = User(id=555, is_bot=False, first_name="Дилшод", language_code="ru")
    chat_id = 555 if chat_type == "private" else -100555
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=user,
        text="/start login",
    )
    if callback:
        query = CallbackQuery(id="q1", from_user=user, chat_instance="c", message=message, data="x")
        return Update(update_id=1, callback_query=query)
    return Update(update_id=1, message=message)


@pytest.fixture
def offline_bot(monkeypatch):  # type: ignore[no-untyped-def]
    """A dispatcher whose dialog calls are recorded and whose Telegram calls go nowhere."""
    from aiogram import Bot
    from aiogram.types import CallbackQuery

    calls: list[str] = []
    answered: list[str] = []

    async def no_send(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    async def answer(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        answered.append(self.id)

    def record(name: str):  # type: ignore[no-untyped-def]
        def stub(*args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(name)
            return Reply("ok")

        return stub

    monkeypatch.setattr(runner, "_send", no_send)
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(runner.dialog, "on_start", record("start"))
    monkeypatch.setattr(runner.dialog, "on_callback", record("callback"))

    def feed(update: object) -> None:
        async def run() -> None:
            bot = Bot(FAKE_TOKEN)
            try:
                await runner.build_dispatcher().feed_update(bot, update)
            finally:
                await bot.session.close()

        asyncio.run(run())

    return calls, answered, feed, monkeypatch


@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
def test_group_chats_are_ignored(offline_bot, chat_type: str) -> None:
    calls, _answered, feed, _ = offline_bot
    feed(_update(chat_type, callback=False))
    feed(_update(chat_type, callback=True))
    assert calls == []


def test_private_chat_reaches_the_dialog(offline_bot) -> None:
    calls, answered, feed, _ = offline_bot
    feed(_update("private", callback=False))
    feed(_update("private", callback=True))
    assert calls == ["start", "callback"]
    assert answered == ["q1"]


def test_callback_is_answered_when_the_dialog_fails(offline_bot) -> None:
    _calls, answered, feed, monkeypatch = offline_bot

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("database down")

    monkeypatch.setattr(runner.dialog, "on_callback", boom)
    feed(_update("private", callback=True))
    assert answered == ["q1"]
