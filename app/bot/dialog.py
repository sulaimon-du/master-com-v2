"""The bot's conversation, as plain sync functions (spec 2026-11-22, D2, D3, D9).

No aiogram here: the aiogram glue calls these through `asyncio.to_thread`. Each function opens
its own session and commits after every service call, refused outcomes included, because
rate-limit counters and used tokens must persist. Tokens and phone numbers are never logged.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.i18n import translate
from app.services import auth, telegram
from app.services.auth import Outcome

CANCEL = "x"


@dataclass(frozen=True)
class Button:
    text: str
    url: str | None = None
    callback: str | None = None


@dataclass(frozen=True)
class Reply:
    text: str
    buttons: tuple[Button, ...] = ()
    ask_contact: bool = False
    remove_keyboard: bool = False


@dataclass(frozen=True)
class TgUser:
    id: int
    first_name: str
    language_code: str | None


def _run[T](fn: Callable[[Session], T]) -> T:
    """Run `fn` in its own session; commit after it, roll back and re-raise on an error."""
    with get_sessionmaker()() as db:
        try:
            result = fn(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return result


def _site(path: str) -> str:
    return get_settings().site_url.rstrip("/") + path


def _lang(db: Session, user: TgUser) -> str:
    return telegram.bot_lang(telegram.user_by_telegram_id(db, user.id), user.language_code)


def _simple(db: Session, user: TgUser, key: str) -> Reply:
    return Reply(translate(_lang(db, user), key))


def on_start(user: TgUser, payload: str) -> Reply:
    def run(db: Session) -> Reply:
        lang = _lang(db, user)
        if payload == "login":
            raw = telegram.issue_signin(
                db,
                telegram_id=user.id,
                first_name=user.first_name,
                language_code=user.language_code,
            )
            if raw is None:
                return Reply(translate(lang, "bot.signin_unavailable"))
            button = Button(
                translate(lang, "bot.signin_button"), url=_site(f"/auth/telegram/{raw}")
            )
            return Reply(translate(lang, "bot.signin_link"), buttons=(button,))
        if payload.startswith("link_"):
            preview = telegram.link_preview(db, payload.removeprefix("link_"))
            if preview is None:
                return Reply(translate(lang, "bot.link_expired"))
            return Reply(
                translate(lang, "bot.link_confirm", account=preview.account),
                buttons=(
                    Button(
                        translate(lang, "bot.link_yes"),
                        callback=telegram.link_callback_data(preview.token_id, user.id),
                    ),
                    Button(translate(lang, "bot.link_no"), callback=CANCEL),
                ),
            )
        if payload == "phone":
            linked = telegram.user_by_telegram_id(db, user.id)
            if linked is None or not auth._is_active(linked):
                return _not_linked(lang)
            return Reply(translate(lang, "bot.ask_phone"), ask_contact=True)
        return Reply(translate(lang, "bot.welcome"))

    return _run(run)


def _not_linked(lang: str) -> Reply:
    button = Button(translate(lang, "bot.open_account"), url=_site("/account"))
    return Reply(translate(lang, "bot.not_linked"), buttons=(button,))


_LINK_OUTCOMES = {
    Outcome.OK: "bot.linked",
    Outcome.TELEGRAM_TAKEN: "bot.telegram_taken",
    Outcome.TELEGRAM_OTHER_LINKED: "bot.telegram_other_linked",
    Outcome.BAD_LINK: "bot.link_expired",
    Outcome.RATE_LIMITED: "bot.rate_limited",
}


def on_callback(user: TgUser, data: str) -> Reply:
    def run(db: Session) -> Reply:
        lang = _lang(db, user)
        if data == CANCEL:
            return Reply(translate(lang, "bot.cancelled"))
        token_id = telegram.token_id_from_callback(data, user.id)
        if token_id is None:
            return Reply(translate(lang, "bot.link_expired"))
        outcome, _ = telegram.confirm_link(db, token_id=token_id, telegram_id=user.id)
        key = _LINK_OUTCOMES.get(outcome, "bot.link_expired")
        # The account may have just gained a Telegram ID, and with it its own language.
        lang = _lang(db, user)
        return Reply(translate(lang, key), ask_contact=outcome is Outcome.OK)

    return _run(run)


_PHONE_OUTCOMES = {
    Outcome.OK: "bot.phone_ok",
    Outcome.ALREADY: "bot.phone_already",
    Outcome.NOT_OWN_CONTACT: "bot.phone_not_own",
    Outcome.PHONE_INVALID: "bot.phone_not_own",
    Outcome.PHONE_TAKEN: "bot.phone_taken",
    Outcome.PHONE_CHANGE_NEEDS_SUPPORT: "bot.phone_change_support",
    Outcome.NOT_LINKED: "bot.not_linked",
    Outcome.RATE_LIMITED: "bot.rate_limited",
}
_KEEP_KEYBOARD = {Outcome.NOT_OWN_CONTACT, Outcome.PHONE_INVALID}


def on_contact(user: TgUser, contact_user_id: int | None, phone_number: str) -> Reply:
    def run(db: Session) -> Reply:
        outcome = telegram.verify_phone(
            db, telegram_id=user.id, contact_user_id=contact_user_id, phone_number=phone_number
        )
        lang = _lang(db, user)
        key = _PHONE_OUTCOMES.get(outcome, "bot.not_linked")
        buttons: tuple[Button, ...] = ()
        if outcome in (Outcome.OK, Outcome.ALREADY, Outcome.NOT_LINKED):
            buttons = (Button(translate(lang, "bot.open_account"), url=_site("/account")),)
        return Reply(
            translate(lang, key),
            buttons=buttons,
            remove_keyboard=outcome not in _KEEP_KEYBOARD,
        )

    return _run(run)


def on_text(user: TgUser) -> Reply:
    return _run(lambda db: _simple(db, user, "bot.help"))
