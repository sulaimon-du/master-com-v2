# Week 4: Telegram sign-in, Telegram linking and phone verification

Status: design, 2026-11-22. The owner asked Claude to decide ("делай сам"), so every choice below
not quoted from BRIEF or DECISIONS is **Claude's call**, to be logged as R-021 and confirmed by
the owner afterwards.

## Why

BRIEF §10 puts "Sign-in: email, Google, Telegram; Telegram bot with phone verification" in weeks
3–4. Email and Google are merged (PR #8). Two things are still missing:

- **Telegram sign-in** (BRIEF §4.1). Most masters in Tajikistan live in Telegram and may have no
  email they check, so for many of them this will be the main way in.
- **Phone verification** (BRIEF §4.2). A master must verify a phone before they can respond or
  appear in search (week 5 onboarding depends on it). The free method is the bot's "Share
  contact"; the phone counts only when the contact belongs to the same Telegram user.

## Goals

1. A visitor can sign in (or sign up) with Telegram, without email, and get a normal session
   (R-011 rules: rotation, cookies, CSRF).
2. A signed-in user can link Telegram in account settings; a Telegram account already linked to
   someone else is refused, never merged (BRIEF §4.1 "no silent merging").
3. A signed-in user can verify a phone through the bot. The phone is stored in E.164 and marked
   verified only when `contact.user_id == from_user.id`. A phone already verified by another
   account is refused (unique index `uq_users_verified_phone`).
4. The bot runs as its own process (R-003): locally in compose, on staging as a second free
   Render web service.
5. All bot and page texts in ru and tg (C9: Claude drafts, owner approves).

## Non-goals (this week)

- Telegram Gateway paid codes (A5: off).
- Notifications through the bot (weeks 8–9), consent for surveyed masters (week 11), claim flow
  for imported profiles (week 11). The bot code should make them easy to add, nothing more.
- Master onboarding (week 5). "Mandatory for masters" is enforced there; here a phone is simply
  verified or not.
- Unlinking Telegram, changing a verified phone. A user who needs it asks support (later: admin).
- Purging old rows (`auth_tokens` etc.): stays a worker follow-up (R-019).

## Anti-goals (would be a failure even if it works)

- Any third-party script, iframe or style on our pages (CSP stays `'self'`).
- A link or code that, forwarded by a victim or sent to a victim, gives an attacker the victim's
  session, or attaches the victim's phone or Telegram to the attacker's account without a clear
  warning in the bot.
- The bot and the web app in one process; async code in the web app.
- A Telegram ID or phone silently moving from one account to another.

## Decisions (Claude's calls)

### D1. Sign-in uses the bot, not the Telegram Login Widget
The widget loads `telegram.org/js/telegram-widget.js` and an iframe from `oauth.telegram.org`.
Both break our CSP (`script-src 'self'`, `frame-src` none, `frame-ancestors 'none'`). Loosening the
CSP for a third-party script on the sign-in page is exactly where an XSS would hurt most. The bot
is needed for phone verification anyway.

### D2. The bot hands out the sign-in link ("magic link"), the site does not
Flow:
1. On `/signin` and `/register`, a "Sign in with Telegram" link points to
   `https://t.me/<bot_username>?start=login`. It carries **no token**.
2. In the bot, `/start login` (or a "Sign in to the site" button) creates a one-time sign-in
   token for **this Telegram user** and replies with an inline URL button
   `https://<site>/auth/telegram/<token>`.
3. GET of that URL shows a page "Sign in as <Telegram first name>?" with a POST button (CSRF).
   GET never signs in: Telegram's link preview and other prefetchers fetch URLs.
4. POST consumes the token (single use, 10 minutes), finds the user by `telegram_id` or creates
   one (name from Telegram, no email, locale from the bot chat language if ru/tg, else the
   browser's `mc_lang`), opens a new session (rotation), and redirects to `/account`.

Why this direction: the token only ever exists inside the victim's own Telegram chat. The
alternative (site creates a token, user confirms in the bot) lets an attacker start a sign-in,
send the deep link to a victim, and get the victim's session after one tap on "Confirm" — the
known QR-login phishing pattern.

Known cost: on phones, Telegram may open the button in its in-app browser, so the session lands
there and not in the browser where the visitor started. Accepted for v1; the page tells the user
they can choose "Open in browser" in Telegram. Revisit if the click-through shows confusion.

### D3. Linking Telegram and verifying a phone start on the site
A signed-in user presses "Link Telegram" or "Verify phone" (POST, CSRF, rate limited). The site
creates a one-time `tg_link` token for that user (30 minutes) and redirects to
`https://t.me/<bot_username>?start=link_<token>`.

In the bot, `/start link_<token>`:
1. Looks up the token without using it. Unknown or expired → "link expired, start again on the
   site".
2. **Confirmation step** (anti-phishing): "This links your Telegram to the Master COM account
   <masked name / masked email, e.g. А*** · a***@gmail.com>. Only continue if this is your
   account." Buttons: "Yes, it's my account" / "Cancel".
3. On "Yes": the token is consumed (single use). If the Telegram ID belongs to another account →
   refuse ("this Telegram is already linked to another account"), nothing changes. If the account
   already has a different Telegram ID → refuse. Otherwise `telegram_id` is saved.
4. Then the bot asks for the contact with a `request_contact` reply keyboard ("Share my phone").
   Phone verification is offered right after linking whether the user started from "Link
   Telegram" or "Verify phone"; the user may ignore it.

Phone step (also reachable later by `/phone` for any linked user):
- Accept only `message.contact` with `contact.user_id == message.from_user.id`. Anything else
  (forwarded contact, someone else's card, typed number) → "please use the button".
- Normalize to E.164 (`+` and digits, Telegram usually sends digits without `+`). Any country is
  accepted; the existing `phone_ok` CHECK decides the format.
- If another account has this phone verified → refuse with a message to contact support. The
  database unique index is the final guard (IntegrityError → same message, never a crash).
- Save `phone` and `phone_verified_at` on the linked account. If the account already has a
  **different** verified phone, refuse ("to change a verified phone, contact support"): a verified
  phone is what makes a master, so changing it is a support action in v1, not a bot tap. Sharing
  the same phone again is a no-op that answers "already verified".
- Reply "Phone verified, go back to the site" with a URL button to `/account`.

### D4. Sessions and privilege
- Telegram sign-in = sign-in: new session ID and CSRF token (same helpers as Google).
- Linking Telegram from the bot does not touch web sessions (the bot has none); the account page
  shows the new state on next load.
- A blocked or deleted account: bot refuses everything; sign-in link refused.

### D5. Where the bot runs
- **Locally:** `bot` service in `compose.yml`, long polling (`python -m app.bot`).
- **Staging:** a second Render free web service `master-com-bot-staging` from the same Docker
  image, command `python -m app.bot`, **webhook mode** (aiogram + its aiohttp web server, which
  comes with aiogram). Render free cannot run a background worker; a sleeping web service is woken
  by Telegram's webhook POST and Telegram retries while it starts. The webhook is checked with
  Telegram's `secret_token` header (`X-Telegram-Bot-Api-Secret-Token`), compared in constant time.
- **Production (week 12, VPS):** polling or webhook, decided then.
- The bot reaches the database directly with the same SQLAlchemy models and **sync service
  functions**, called from aiogram handlers through `asyncio.to_thread`. The web app never imports
  aiogram; the bot never imports FastAPI routers.

### D6. Data
- `auth_tokens.purpose` gets one more value, `tg_link` (migration updates the CHECK). It belongs
  to a web user like the other purposes and is stored hashed.
- Sign-in tokens may belong to nobody yet (a new Telegram user), so they get their own table
  `telegram_signins`: `id`, `token_hash` (unique), `telegram_id bigint`, `first_name` (≤ 80),
  `language_code` (≤ 8, nullable), `expires_at`, `used_at`, `created_at`. The account is created
  only when the browser POSTs, never when someone just presses Start in the bot.
- No new table for bot state: the confirmation step stores nothing; the "Yes" button's callback
  data carries a short reference to the token (e.g. its ID), and the bot re-checks everything on
  the click.

### D7. Limits (all in Postgres, R-019 style)
- Bot `/start login`: 5 sign-in links per Telegram user per 15 minutes.
- Bot `/start link_…` and contact messages: 20 per Telegram user per hour.
- Site "Link Telegram" / "Verify phone": 10 per user per hour and 30 per IP per 15 minutes.
- Site POST `/auth/telegram/<token>`: shares the sign-in per-IP limit (30 / 15 min).

### D7b. CSP
"Link Telegram" / "Verify phone" are POST forms answered by a redirect to `https://t.me/...`, so
`form-action` gets `https://t.me` (same reason as Google in R-019). Nothing else changes.

### D8. Settings
`TELEGRAM_BOT_TOKEN` (secret), `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET` (secret, staging),
`TELEGRAM_WEBHOOK_URL` (staging bot service URL; unset → polling), `SITE_URL` (absolute base for
links the bot sends; the bot has no request to build them from). Telegram buttons on the site are
hidden unless the token and username are set (like Google).

**Two bots, one per environment.** Telegram refuses polling while a webhook is set, so one bot
cannot serve both local polling and the staging webhook. The owner creates two bots in BotFather:
a development bot (token in local `.env`, polling) and a staging bot (token in Render, webhook).
Production gets its own third bot in week 12. The bot refuses to start in polling mode when
`APP_ENV` is staging or production, so a staging token pasted into a laptop cannot silently
drop the staging webhook.

### D9. Texts
Bot texts in `app/locales/{ru,tg}.json` under `bot.*`, chosen by the user's account locale, else
the Telegram `language_code` (`tg` → tg, anything else → ru). Owner approves (C9).

## Security checklist (from CLAUDE.md, for the reviewer)
- Every write checks ownership in a service; the bot acts only on the account bound by a consumed
  token or by `telegram_id`.
- No token in logs (log token IDs, never values). Bot token never in logs or errors.
- Webhook secret compared with `hmac.compare_digest`.
- "Someone else tries" tests: another Telegram user uses my sign-in link; my link token used by
  another Telegram user; contact of another person; Telegram already linked elsewhere; phone
  verified elsewhere.

## Left to the builder
- aiogram version (latest before the `exclude-newer` cutoff, Python 3.12) and the handler layout.
- Masking format for the confirmation text, exact button wording (owner reviews texts anyway).
- How the account page shows Telegram and phone state (follow the Google card).
- Test approach for handlers: services carry the logic and get the tests; handlers stay thin and
  get a few tests with aiogram's objects built in memory, no network.
