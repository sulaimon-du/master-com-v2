# Telegram sign-in, linking and phone verification: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Telegram sign-in through a bot-issued one-time link, Telegram linking and phone verification through the bot, and the bot as its own process (compose locally, Render web service in webhook mode on staging).

**Architecture:** All rules live in a sync service `app/services/telegram.py` (SQLAlchemy, no aiogram). A sync "dialog" layer `app/bot/dialog.py` turns bot events into replies (plain dataclasses) and is fully testable without aiogram or the network. `app/bot/runner.py` is thin aiogram glue that calls the dialog through `asyncio.to_thread`. The web app gets two pages and one POST; it never imports aiogram.

**Tech Stack:** FastAPI (sync), SQLAlchemy 2.0, Alembic, Postgres 16, aiogram 3 (latest release before the `exclude-newer` cutoff; it brings aiohttp for webhook mode), pytest on real Postgres.

**Spec:** `docs/superpowers/specs/2026-11-22-telegram-signin-and-phone-design.md`

## Global Constraints

- CSP stays `'self'` for scripts, styles, frames; only `form-action` gains `https://t.me` (spec D7b).
- No aiogram import anywhere under `app/web`, `app/services`, `app/models`, `app/core`; the web app stays sync.
- Tokens: `new_token()` (256 bits), stored as `hash_token()`; values never logged (log IDs only). Bot token never logged.
- Sign-in token TTL 10 minutes; link token TTL 30 minutes; both single use (spec D2, D3).
- Limits (spec D7): bot sign-in links 5 per Telegram user / 15 min; bot link/contact actions 20 per Telegram user / hour; site link start 10 per user / hour and 30 per IP / 15 min; POST `/auth/telegram/{token}` uses scope `login_ip` (30 / 15 min, same counter as password sign-in).
- Phone: E.164, must match `^[+][1-9][0-9]{6,14}$`; only `contact.user_id == from_user.id`; a different verified phone already on the account → refuse (support); verified elsewhere → refuse.
- No silent merging: Telegram ID on another account → refuse; account with a different Telegram ID → refuse.
- User-facing text only through `app/locales/{ru,tg}.json` (bot texts under `bot.*`); key parity test must stay green.
- Bot language: account locale, else Telegram `language_code == "tg"` → `tg`, else `ru` (spec D9).
- Polling is refused when `APP_ENV` is `staging` or `production` (spec D8).
- Every write endpoint gets a "someone else tries" test (CLAUDE.md). Checks before each commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -o addopts=""`.

## Review Focus

1. **Forged callback data.** A Telegram client can send any `callback_data`. Pressing "Yes, it's my account" must only work for the Telegram user who received that button: callback data carries an HMAC over (token id, telegram id) keyed with `SECRET_KEY`. Test in Task 4.
2. **Link previewers.** Telegram and messengers fetch URLs; GET `/auth/telegram/{token}` must not consume the token. Test in Task 5.
3. **Account blocked between steps.** Sign-in POST and link confirmation re-check `_is_active` under a row lock (like `_consume_token`). Tests in Tasks 3 and 4.
4. **Contact sent as text or forwarded.** A typed number, a forwarded contact or someone else's card is refused with "use the button". Test in Task 4.
5. **Double tap / two tabs.** Pressing "Yes" twice, or posting the sign-in form twice, gives one success and then "link expired", never a 500. Tests in Tasks 3 and 4.

---

### Task 1: Settings, CSP and the data model

**Files:**
- Modify: `app/core/config.py`, `app/core/security_headers.py`, `app/models/auth.py`, `app/models/__init__.py`, `.env.example`, `docs/DATA_MODEL.md`
- Create: `migrations/versions/<timestamp>_0005_telegram.py`
- Test: `tests/test_constraints.py`, `tests/test_security.py`

**Interfaces:**
- Produces: `Settings.telegram_bot_token: SecretStr | None`, `telegram_bot_username: str | None`, `telegram_webhook_secret: SecretStr | None`, `telegram_webhook_url: str | None`, `site_url: str = "http://localhost:8000"`; property `Settings.telegram_enabled -> bool` (token and username set).
- Produces: `TOKEN_PURPOSES = ("confirm_email", "reset_password", "tg_link")`; model `TelegramSignin(IdPk, CreatedAt, Base)`, table `telegram_signins`: `token_hash String(64) unique`, `telegram_id BigInteger not null`, `first_name String(80) not null`, `language_code String(8) null`, `expires_at timestamptz not null`, `used_at timestamptz null`, index on `telegram_id`.

- [ ] **Step 1: Failing tests**
  - `test_auth_token_accepts_tg_link_purpose` (insert `purpose='tg_link'` succeeds) and `test_auth_token_rejects_unknown_purpose` (`'tg_other'` → IntegrityError) in `tests/test_constraints.py`.
  - `test_telegram_signin_token_hash_is_unique` in `tests/test_constraints.py`.
  - `test_csp_allows_forms_to_redirect_to_telegram`: CSP header contains `form-action 'self' https://accounts.google.com https://t.me`, and still `script-src 'self'`, `frame-ancestors 'none'`.
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest -q -o addopts="" tests/test_constraints.py tests/test_security.py`
- [ ] **Step 3: Implement** settings, CSP, model, migration 0005 (`alembic revision --autogenerate -m "telegram"`, then hand-edit: CHECK `ck_auth_tokens_purpose` dropped and recreated with the three values; downgrade restores two values after deleting `tg_link` rows). Add the five env names to `.env.example` with comments; add `telegram_signins` row and `tg_link` purpose to `docs/DATA_MODEL.md`.
- [ ] **Step 4: Run** `uv run alembic upgrade head && uv run alembic check && uv run alembic downgrade -1 && uv run alembic upgrade head`, then the tests: PASS.
- [ ] **Step 5: Commit** `feat(telegram): settings, CSP form-action and data model`

### Task 2: Phone normalization, masking and bot language helpers

**Files:**
- Create: `app/services/telegram.py` (helpers section)
- Test: `tests/test_telegram_service.py`

**Interfaces:**
- Produces: `normalize_phone(raw: str) -> str | None`; `mask_account(user: User) -> str`; `bot_lang(user: User | None, language_code: str | None) -> str`.

- [ ] **Step 1: Failing tests**
  - `normalize_phone("992901234567") == "+992901234567"`; `normalize_phone("+992 90 123-45-67") == "+992901234567"`; `normalize_phone("123") is None`; `normalize_phone("0901234567") is None` (leading 0 after `+` not allowed).
  - `mask_account(User(name="Ана", email="ana@gmail.com")) == "А** · a**@gmail.com"`; with no email → `"А**"`; with no name and no email → `"—"`. Rule: keep the first character of the name and of the email's local part, the rest of each becomes `**`, domain kept.
  - `bot_lang(None, "tg") == "tg"`, `bot_lang(None, "en") == "ru"`, `bot_lang(None, None) == "ru"`, `bot_lang(User(locale="tg"), "ru") == "tg"`.
- [ ] **Step 2: Run, expect FAIL** (ImportError).
- [ ] **Step 3: Implement** the three functions; `normalize_phone` keeps digits, prefixes `+`, validates with `PHONE_RE` from `app/models/common.py`.
- [ ] **Step 4: Run, PASS.**
- [ ] **Step 5: Commit** `feat(telegram): phone normalization and masking helpers`

### Task 3: Sign-in service

**Files:**
- Modify: `app/services/telegram.py`, `app/services/auth.py` (add `Outcome.TELEGRAM_TAKEN`, `Outcome.TELEGRAM_OTHER_LINKED`, `Outcome.PHONE_TAKEN`, `Outcome.PHONE_CHANGE_NEEDS_SUPPORT`, `Outcome.NOT_OWN_CONTACT`, `Outcome.NOT_LINKED`, `Outcome.ALREADY`)
- Test: `tests/test_telegram_service.py`

**Interfaces:**
- Consumes: `TelegramSignin`, `auth.open_session`, `auth._is_active`, `rate_limit.allow`, `new_token`, `hash_token`.
- Produces:
  - `SIGNIN_TTL = timedelta(minutes=10)`
  - `issue_signin(db, *, telegram_id: int, first_name: str, language_code: str | None) -> str | None` — raw token, or None when rate limited or the Telegram ID belongs to a blocked/deleted account.
  - `@dataclass(frozen=True) class SigninPreview: first_name: str`
  - `signin_preview(db, raw: str) -> SigninPreview | None` — read only, None when unknown/used/expired.
  - `sign_in_with_telegram(db, *, ip: str, raw: str, lang: str) -> tuple[auth.Outcome, User | None]` — `RATE_LIMITED` / `BAD_LINK` / `OK`.

- [ ] **Step 1: Failing tests**
  - `test_signin_creates_an_account_without_email`: issue for telegram_id 777, first_name "Ана", language "tg"; sign in → `OK`, user has `telegram_id=777`, `email is None`, `name == "Ана"`, `locale == "tg"`.
  - `test_signin_finds_the_existing_account_by_telegram_id` (no second user created).
  - `test_signin_token_works_once` (second call → `BAD_LINK`) and `test_signin_token_expires_after_10_minutes` (set `expires_at` in the past → `BAD_LINK`).
  - `test_preview_does_not_use_the_token` (preview twice, then sign in → `OK`).
  - `test_blocked_account_gets_no_signin_link` (`issue_signin` → None) and `test_account_blocked_after_the_link_was_issued_cannot_sign_in` (`BAD_LINK`).
  - `test_signin_links_are_limited_per_telegram_user` (6th `issue_signin` within 15 min → None).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Consume with `UPDATE telegram_signins SET used_at=now() WHERE token_hash=… AND used_at IS NULL AND expires_at > now() RETURNING telegram_id, first_name, language_code`; then `SELECT … FROM users WHERE telegram_id=… FOR UPDATE` (populate_existing); create the user if absent (`IntegrityError` on a concurrent create → re-select). Rate limits: `tg_signin_issue` per telegram id (5 / 15 min); `login_ip` per IP (30 / 15 min) on sign-in. Locale: `language_code == "tg"` → tg, else the `lang` argument.
- [ ] **Step 4: Run, PASS.**
- [ ] **Step 5: Commit** `feat(telegram): sign-in service with one-time bot links`

### Task 4: Linking and phone service

**Files:**
- Modify: `app/services/telegram.py`
- Test: `tests/test_telegram_service.py`

**Interfaces:**
- Consumes: `auth._issue_token`, `auth._consume_token` pattern, `Outcome` values from Task 3.
- Produces:
  - `LINK_TTL = timedelta(minutes=30)`
  - `allow_link_start(db, *, user_id: int, ip: str) -> bool` (10 / hour per user, 30 / 15 min per IP)
  - `issue_link(db, user: User) -> str` — raw `tg_link` token.
  - `@dataclass(frozen=True) class LinkPreview: token_id: int; account: str` (masked)
  - `link_preview(db, raw: str) -> LinkPreview | None` — read only.
  - `link_callback_data(token_id: int, telegram_id: int) -> str` — `"l:<token_id>:<16 hex of HMAC-SHA256(secret_key, f'{token_id}:{telegram_id}')>"`, ≤ 64 bytes.
  - `token_id_from_callback(data: str, telegram_id: int) -> int | None` — constant-time check.
  - `confirm_link(db, *, token_id: int, telegram_id: int) -> tuple[auth.Outcome, User | None]` — `OK` / `BAD_LINK` / `TELEGRAM_TAKEN` / `TELEGRAM_OTHER_LINKED` / `RATE_LIMITED`.
  - `verify_phone(db, *, telegram_id: int, contact_user_id: int | None, phone_number: str) -> auth.Outcome` — `OK` / `ALREADY` / `NOT_OWN_CONTACT` / `NOT_LINKED` / `PHONE_TAKEN` / `PHONE_CHANGE_NEEDS_SUPPORT` / `RATE_LIMITED`.

- [ ] **Step 1: Failing tests**
  - `test_confirm_link_attaches_telegram_to_the_token_owner` and `test_link_token_works_once`.
  - `test_callback_data_from_another_telegram_user_is_rejected` (`token_id_from_callback(data_for_777, 888) is None`) — Review Focus 1.
  - `test_telegram_linked_to_another_account_is_refused` (`TELEGRAM_TAKEN`, nothing changes) and `test_account_with_a_different_telegram_is_refused` (`TELEGRAM_OTHER_LINKED`).
  - `test_blocked_account_cannot_be_linked` (`BAD_LINK`) — Review Focus 3.
  - `test_own_contact_verifies_the_phone` (`phone == "+992901234567"`, `phone_verified_at` set).
  - `test_someone_elses_contact_is_refused` (`contact_user_id=999` for telegram 777 → `NOT_OWN_CONTACT`) and `test_contact_without_user_id_is_refused` (None → `NOT_OWN_CONTACT`) — Review Focus 4.
  - `test_phone_verified_elsewhere_is_refused` (`PHONE_TAKEN`), `test_changing_a_verified_phone_needs_support` (`PHONE_CHANGE_NEEDS_SUPPORT`), `test_same_phone_again_is_already` (`ALREADY`), `test_unlinked_telegram_cannot_verify` (`NOT_LINKED`).
  - `test_second_confirm_click_is_bad_link_not_error` — Review Focus 5.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** `confirm_link` locks the token owner (`FOR UPDATE`, populate_existing) before consuming the token by ID (`UPDATE auth_tokens … WHERE id=… AND purpose='tg_link' AND used_at IS NULL AND expires_at > now() RETURNING user_id`), same order as `_consume_token`. Telegram ID uniqueness: check, then flush in `begin_nested`; `IntegrityError` → `TELEGRAM_TAKEN`. Phone: check, flush in `begin_nested`; `IntegrityError` → `PHONE_TAKEN`. Rate limit scope `tg_action` per telegram id (20 / hour) on `confirm_link` and `verify_phone`.
- [ ] **Step 4: Run, PASS.**
- [ ] **Step 5: Commit** `feat(telegram): linking and phone verification service`

### Task 5: Web pages and account card

**Files:**
- Create: `app/web/telegram.py`, `app/templates/pages/telegram_signin.html`
- Modify: `app/main.py` (router), `app/web/auth.py` (NOTICES), `app/templates/pages/signin.html` or the sign-in partial, `app/templates/pages/register.html`, `app/templates/pages/account.html`, `app/schemas/account.py` (`telegram_linked: bool`, `phone: str | None`, `phone_verified: bool`), `app/locales/{ru,tg}.json`
- Test: `tests/test_auth_telegram_web.py`

**Interfaces:**
- Consumes: Tasks 3 and 4 services; `_attach_session`, `notice_page`, `CSRFProtected`, `current_user`, `client_ip`, `DbSession`.
- Produces routes: `GET /auth/telegram/{token}` (`telegram_signin_page`), `POST /auth/telegram/{token}` (`telegram_signin_submit`, CSRF), `POST /account/telegram/link` (`telegram_link`, CSRF) → 303 to `https://t.me/<username>?start=link_<raw>`.
- Template context: `telegram_enabled`, `telegram_signin_url = f"https://t.me/{username}?start=login"`, `telegram_phone_url = f"https://t.me/{username}?start=phone"`.

- [ ] **Step 1: Failing tests**
  - `test_telegram_button_hidden_when_not_configured` / `test_telegram_button_links_to_the_bot` (`https://t.me/MasterComTestBot?start=login` on `/signin`).
  - `test_signin_page_get_does_not_use_the_token` (two GETs, then POST signs in) — Review Focus 2.
  - `test_signin_post_needs_csrf` (403) and `test_signin_post_rotates_the_session` (new session cookie, `/account` 200, redirect `/account`).
  - `test_bad_or_used_signin_link_shows_a_friendly_page` (400, no 500); double POST → second is 400 — Review Focus 5.
  - `test_link_start_redirects_to_the_bot_with_a_token` (location starts `https://t.me/MasterComTestBot?start=link_`, token length 43).
  - `test_link_start_needs_a_signed_in_user` and `test_link_start_is_rate_limited` (11th in an hour → 429).
  - `test_account_shows_telegram_and_phone_state` (linked + verified phone shows the number; not linked shows the link button).
  - `test_new_locale_keys_exist_in_both_languages` is covered by the existing parity test; run it.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Texts (ru drafts; tg drafts for owner review): `telegram.signin_button` "Войти через Telegram", `telegram.signin_title` "Вход через Telegram", `telegram.signin_confirm` "Войти как {name}?", `telegram.signin_submit` "Войти", `telegram.signin_in_app_hint` "Если страница открылась внутри Telegram, можно выбрать «Открыть в браузере».", `telegram.link_bad` "Ссылка устарела. Попросите новую в боте.", `account.telegram`, `account.telegram_linked`, `account.telegram_link` "Привязать Telegram и подтвердить телефон", `account.phone`, `account.phone_verified` "подтверждён", `account.phone_verify` "Подтвердить телефон в Telegram", `account.telegram_unavailable`.
- [ ] **Step 4: Run** the new tests and the whole suite: PASS.
- [ ] **Step 5: Commit** `feat(telegram): sign-in page, link start and account card`

### Task 6: Bot dialog (sync, no aiogram)

**Files:**
- Create: `app/bot/__init__.py`, `app/bot/dialog.py`
- Modify: `app/locales/{ru,tg}.json` (`bot.*`)
- Test: `tests/test_bot_dialog.py`

**Interfaces:**
- Consumes: Tasks 2–4 services, `get_sessionmaker`, `get_settings`, `translate`.
- Produces:
  - `@dataclass(frozen=True) class Button: text: str; url: str | None = None; callback: str | None = None`
  - `@dataclass(frozen=True) class Reply: text: str; buttons: tuple[Button, ...] = (); ask_contact: bool = False; remove_keyboard: bool = False`
  - `@dataclass(frozen=True) class TgUser: id: int; first_name: str; language_code: str | None`
  - `on_start(user: TgUser, payload: str) -> Reply` — payload `""`, `"login"`, `"phone"`, `"link_<token>"`.
  - `on_callback(user: TgUser, data: str) -> Reply`
  - `on_contact(user: TgUser, contact_user_id: int | None, phone_number: str) -> Reply`
  - `on_text(user: TgUser) -> Reply` (anything else: short help)
  Each opens its own session, commits on success, and never raises for expected outcomes.

- [ ] **Step 1: Failing tests** (real DB, no network):
  - `/start login` → one URL button `https://<site_url>/auth/telegram/<43-char token>`; text from `bot.signin_link`.
  - `/start link_<raw>` for a valid token → text contains the masked account and two callback buttons, "Yes" carrying `link_callback_data(...)`; invalid token → `bot.link_expired`.
  - callback "Yes" → account linked and reply `ask_contact=True`; callback forged for another Telegram user → `bot.link_expired` — Review Focus 1.
  - `/start phone` for an unlinked user → `bot.not_linked` with a URL button to `<site_url>/account`; for a linked user → `ask_contact=True`.
  - `on_contact` with own contact → `bot.phone_ok`, `remove_keyboard=True`, URL button to `/account`; others map one-to-one to `bot.phone_not_own`, `bot.phone_taken`, `bot.phone_change_support`, `bot.phone_already`, `bot.not_linked`, `bot.rate_limited`.
  - Language: Telegram `language_code="tg"` with no account → Tajik text.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** with ru texts drafted for every `bot.*` key and tg drafts (owner approves, C9).
- [ ] **Step 4: Run, PASS.**
- [ ] **Step 5: Commit** `feat(bot): sync dialog for sign-in, linking and phone`

### Task 7: aiogram runner, compose, Render and docs

**Files:**
- Create: `app/bot/runner.py`, `app/bot/__main__.py`
- Modify: `pyproject.toml`, `uv.lock` (add `aiogram`), `compose.yml` (service `bot`: same image, `command: python -m app.bot`, depends on db, env from `.env`), `render.yaml` (second web service `master-com-bot-staging`, `dockerCommand: python -m app.bot`, env `APP_ENV=staging`, `DATABASE_URL` sync:false, `SECRET_KEY` sync:false (must equal the web service's, for callback HMAC), `TELEGRAM_BOT_TOKEN` / `TELEGRAM_WEBHOOK_SECRET` sync:false, `TELEGRAM_BOT_USERNAME=MasterComStagingBot`, `TELEGRAM_WEBHOOK_URL`, `SITE_URL=https://master-com-staging.onrender.com`; no migrations, no seed), the web service gets `TELEGRAM_BOT_USERNAME` and `SITE_URL`; `README.md` (bot section, endpoints), `docs/DECISIONS.md` (R-021 from the spec's decisions), `docs/PRODUCT_RULES.md` (C9 status)
- Test: `tests/test_bot_runner.py`

**Interfaces:**
- Consumes: Task 6 dialog.
- Produces: `build_dispatcher() -> aiogram.Dispatcher`; `check_webhook_secret(header: str | None, expected: str) -> bool`; `main() -> None` (polling when `telegram_webhook_url` is unset, else webhook on `0.0.0.0:$PORT` at path `/telegram/webhook` with `secret_token`, plus `GET /health` → 200 for Render).

- [ ] **Step 1: Failing tests**
  - `test_webhook_secret_must_match` (wrong / missing → False, right → True).
  - `test_polling_is_refused_on_staging` (`APP_ENV=staging`, no webhook URL → `main()` raises `SystemExit` with a message, no network call).
  - `test_web_app_does_not_import_aiogram` (import `app.main`, create the app; `"aiogram" not in sys.modules` in a subprocess).
  - `test_dispatcher_builds` (`build_dispatcher()` returns a Dispatcher with message and callback handlers registered).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Handlers: `CommandStart(deep_link=True)` → `dialog.on_start`, `F.contact` → `on_contact`, callback queries → `on_callback` (answer the callback), other messages → `on_text`; each through `asyncio.to_thread`; `Reply` → `InlineKeyboardMarkup` for URL/callback buttons, `ReplyKeyboardMarkup(request_contact=True, one_time_keyboard=True)` when `ask_contact`, `ReplyKeyboardRemove` when `remove_keyboard`. Log update types and outcomes, never tokens or phone numbers.
- [ ] **Step 4: Run** all checks: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -o addopts="" && uv run alembic check && uv run pip-audit`; then `docker compose up --build bot` with the dev bot: `/start` in Telegram answers.
- [ ] **Step 5: Commit** `feat(bot): aiogram runner, compose service and Render staging bot`

### After the tasks (owner steps, listed in the PR)
1. Render: create the bot service from the Blueprint (or by hand), paste `DATABASE_URL`, `SECRET_KEY` (same as web), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`; add `TELEGRAM_BOT_USERNAME` and `SITE_URL` to the web service.
2. Click-through on staging: sign in with Telegram from the phone; link Telegram from a password account; verify the phone; try someone else's contact.
3. Approve the Tajik `bot.*` and `telegram.*` texts (C9).
