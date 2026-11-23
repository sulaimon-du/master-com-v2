# Master COM redefinition: decisions log

Supersedes the old 14_DECISIONS.md. The new brief is `redefinition/BRIEF.md`. Numbering R-xxx to avoid clashing with D-001..D-100.

## R-001 Backend stack (2026-10-22)
- Python + FastAPI, **sync** routes, SQLAlchemy 2.0 + Alembic, PostgreSQL.
- No async/sync mixing inside the web app.
- No Rust in v1. Revisit only if production profiling shows a concrete bottleneck; then a single PyO3 module or a small separate service.
- Rejected: Django (user's choice), Go (slower to v1: admin and more glue hand-written), Rust (second language, no bottleneck to justify it).

## R-002 Frontend (2026-10-22)
- Server-rendered Jinja2 templates + HTMX + Alpine.js + Tailwind (compiled with CLI). One application, PWA.
- No separate SPA. No public API in v1; JSON endpoints added when a mobile app is needed.

## R-003 Supporting libraries (2026-10-22)
- Admin: SQLAdmin.
- Images: Pillow.
- Telegram bot: aiogram, run as a **separate process/container** (it is async-only). The web app sends notifications with plain sync HTTP calls to the Telegram Bot API, so the "no mixing" rule holds per process.

## R-004 Scope and time (round 3)
- Full v1 scope in 3 months at 12–20 h/week.
- Reviews ship in v1 (code ready; reviews appear with the first completed requests).
- If behind schedule, postpone in this order: chat → web push → reviews.

## R-005 Chat (round 3)
- Only between client and master inside a request, after a response.
- v1 content: text, photos, "send address" button.
- Delivery: polling every few seconds while the chat is open + Telegram notification. No WebSocket in v1.
- Chat photos are compressed (WebP, long side 1600 px, EXIF/GPS stripped) like portfolio photos.

## R-006 Moderation (round 3)
- Post-moderation: profiles and requests publish immediately; owner reviews after and on complaints.
- Single moderator (owner).

## R-007 Hosting and data (round 3)
- Before launch: free staging. After launch: one EU VPS (~€6.5/month) with Docker.
- Postgres on the same VPS, nightly backups to object storage, restore tested.
- Images: Cloudflare R2, processed on our server.
- Domain: .tj (foreigners may register through a local registrar; a-z, 0-9, hyphen, 2–63 chars). Defensive .com too, if cheap.

## R-008 Quality without manual review (round 3)
- Mandatory tests on auth, access control, requests, chat.
- CI blocks merge on failing tests, lint or vulnerability scan.
- A separate Claude agent reviews every PR without having seen the code being written.
- Weekly click-through of new features by the owner against a checklist.
- Separate security audit before launch.
- New repository; the old one stays as an archive.

## R-009 Consent and seeding (2026-10-22)
- Consent of surveyed masters is recorded with a one-tap confirmation in the Telegram bot; date and time stored in the database.
- Scraping and publishing other people's data were on hold until a lawyer in Dushanbe was consulted (Law No. 1537 of 3 Aug 2018 "On the Protection of Personal Data"). *Superseded by R-015.*

## R-010 SEO pages in v1 (2026-10-22)
- Generated landing pages `/<city>/<specialty>` and `/<city>/<district>/<specialty>`, in ru and tg.
- Indexed only with ≥ 3 masters; otherwise `noindex`. Sitemap, canonical, hreflang, OG tags.

## R-011 Platform choices confirmed (2026-10-22)
- **Sessions instead of JWT**, with safeguards: 256-bit random ID stored hashed; `__Host-` cookie, HttpOnly, Secure, SameSite=Lax; new ID at sign-in and privilege change; 30-day idle / 90-day absolute lifetime; instant logout and "sign out everywhere"; blocked users' sessions revoked at once; CSRF token on every form and HTMX request.
- Postgres-backed job queue (`FOR UPDATE SKIP LOCKED`) and rate-limit counters; no Redis in v1.
- Caddy as reverse proxy with automatic HTTPS.
- Account deletion removes personal data; reviews stay anonymized.
- Nightly backups kept 14 days; monthly restore test.
- Phone verification optional for clients, mandatory for masters.

## R-012 Look and language (round 4)
- Soft rounded corners. Portfolio: square 1:1 grid, full uncropped photo on tap.
- Dark theme ships in v1 (device setting + manual switch).
- Logo by a designer; Claude writes the designer brief once the name is chosen.
- Brand name in Latin everywhere.
- The owner proofreads Tajik names and the UI translation.
- "Usto" rejected: usto.tj is a live service for finding repair masters in Tajikistan (direct competitor).

## R-013 Open questions resolved (2026-10-22)
- All proposals in `open_questions.md` approved, with these changes:
  - C3: all price units allowed: fixed, per m², per running metre, per piece, per hour.
  - D1/D2: owner runs two design research prompts (`design_research_prompts.md`); round 5 screens follow the findings.
  - E3: no registered business for now.
  - E4: support = email + in-site support question that becomes a temporary chat once the owner answers:
    - signed-in users: thread in the site, polling like request chat; guests: leave an email, replies go by email;
    - owner answers in the admin panel and gets a Telegram alert for each new question;
    - thread closes after 7 days without messages; a new question opens a new thread.
- Competitor analysis skipped by the owner's decision. Positioning moved to round 6.
- "master" as a name: generic word, weak as a trademark and in search; kept as working name; final name in round 6. Domain status of master.tj / mastercom.tj not verified (owner checks via a .tj registrar whois).

## R-014 Visual direction, tentative (2026-10-24)
- Owner's current pick: **variant 6 "Typographic" look** (ink `#111111`, white cards, signal-yellow `#F5C400` accent, black buttons turning yellow on hover, 12 px corners) **with the typefaces of variant 2 "Magazine"**: Inter for the UI, Cormorant Garamond (700) for headings.
- Marked tentative ("for now"); added to the design lab as variant 11 "Pick: 6 + fonts of 2" and set as its default.
- Risks to watch:
  - Cormorant Garamond is thin at small sizes, so keep it for large headings (H1, hero, project titles) and use Inter for small headings;
  - large type takes space from photos on phones.

## R-015 Seeding by scraping approved (2026-10-24)
- The owner's lawyer approved scraping and publishing masters' profiles (supersedes the hold in R-009). The owner keeps the approval in writing, with its scope.
- Flow, per round 2: scraped profiles are published, and the master claims the profile by verifying the phone.
- Safeguards built in:
  - each imported record stores its source URL and import date;
  - unclaimed profiles carry a "Not yet confirmed by the master" label;
  - a "Remove my data" request on every unclaimed profile, handled in the admin panel; removed people are never re-imported;
  - scraping respects each source site's terms and robots.txt, at a low request rate;
  - imported data is limited to what the source shows publicly: name, specialty, city or district, public contact; work photos only where the source allows reuse.
- Scope: an import tool in the admin panel plus the claim flow, about 3–5 extra days, placed in weeks 10–11.

## R-016 Contact for unclaimed profiles (2026-10-24)
- Unclaimed (imported) profiles show a "Call" button with the phone from the source, instead of responses and chat.
- The number is visible to signed-in users only, like all contacts.
- Taps are counted and shown to the master on claiming; a "wrong number" report hides the profile until admin review.

## R-017 Staging live (2026-10-24)
- Staging: Render free web service (Docker, Frankfurt) + Neon free Postgres (Frankfurt), from `render.yaml`. URL: https://master-com-staging.onrender.com
- Push to `main` deploys automatically; migrations run on start. Free service sleeps after 15 minutes idle.
- Week 1 (walking skeleton) done: CI green on GitHub (lint, tests, audit, Docker build), `/health/ready` returns ok with the database.

## R-018 Data model choices (2026-10-24, proposed; owner to confirm)
Week 2. Details in `docs/DATA_MODEL.md`. Points where the brief was silent or needed a reading:
- **Places:** the top level ("city") holds both cities (шаҳр) and rural districts (ноҳия), with a region and a kind; the second level ("district") holds city subdivisions, so far only Dushanbe's four. A request needs a district only when its city has districts. *Alternative:* region → city/nohiya → (district); it would change the SEO URLs, so I did not take it.
- **Complaint reasons:** C7's list plus `wrong_number` for unclaimed profiles (R-016).
- **Request end states:** `closed` = ended by the client or by the 30-day auto-close (C4); `cancelled` = withdrawn by the client.
- **Text limits not in the brief:** response message 500 characters, collection title 120, collection description 1000.
- **Rating constants (BRIEF §4.11):** score = (C·m + sum of ratings) / (C + n), with C = 5 and m = 4.0. Proposal; can be tuned without a migration.
- **Kept in services, not in the database:** at most 10 specialties and 3 primary; collection and photo limits (plan config); no response to one's own request; reviews only by the client of a completed request. These arrive with their features (weeks 5–10).
- **Re-import blocklist:** stores an HMAC-SHA256 (keyed with a server secret) of the phone or source URL of people who asked to be removed, not the value. A plain SHA-256 would not do: all 10^9 local phone numbers can be hashed in seconds.
- **Deleting a profile:** "Remove my data" deletes an unclaimed profile together with its complaints, claims and work data. Unclaimed profiles cannot respond or be reviewed, so nothing else blocks the delete.
- **Later, before week 7:** old place names people still type (Курган-Тюбе, Чкаловск, Кайраккум…) need place synonyms for search. Renaming a seeded slug needs a manual deactivation of the old row.
- **Seed:** reference data loads with `python -m app.seed` on every start (`RUN_SEED=1`); it is idempotent. Until the admin panel can edit these tables (week 10) the seed files are the source of truth: names are overwritten from the files and seeded synonyms are synced. In week 10, switch to insert-only so admin edits win.

## R-019 Sign-in and sessions implementation (2026-10-24, week 3)
Implements BRIEF §4.1 and R-011. Choices and deviations, for the owner to confirm:
- **Password rule:** 10 to 128 characters, no composition rules. Argon2id with argon2-cffi defaults. Sign-in accepts any length so existing passwords keep working.
- **Confirmation before password sign-in:** an unconfirmed account cannot sign in, and its answer is the same 401 and text as a wrong password ("wrong email or password; if you just registered, confirm your email first"). With the right password it also quietly gets a fresh confirmation link (at most 3 per address per hour, on its own counter that never shows as 429). "Resend" has its own per-address limit, also silent: over it nothing is sent and the answer stays the same, so no counter can be observed from outside. A separate "confirm your email" answer would let anyone test an address by registering it and then signing in. Confirmation links are single-use and expire after 48 hours; reset links expire after 1 hour and also confirm the address.
- **Confirmation needs the password (pre-hijack protection):** the confirmation page asks for the account's password and then signs the owner in. A link alone is not enough: a stranger could register someone's address with their own password and wait for the owner to confirm it from the inbox. A wrong password keeps the link valid and points to password reset, which proves the inbox, replaces the stranger's password, confirms the address and burns open confirmation links. Password tries through a link count against the same 5-per-15-minutes limit as sign-in for that address, plus 30 per IP.
- **Google and an unconfirmed account (owner approved 2026-10-25, with admin approval):** if Google returns a verified email that matches an account nobody has confirmed, and the account has no other proven method (no Google, Telegram or verified phone), a request goes to the owner (`google_claims`) and the user sees "request sent". After the owner approves, the next Google sign-in hands the account over: the unproven password, its links and sessions are dropped. The handover re-checks that the account is still unproven and has the same email, so an approval does nothing if the real owner confirmed it meanwhile. Until the admin panel (week 10) the owner decides with `uv run python -m app.cli claims list|approve <id>|reject <id>`, or on staging with one SQL statement in the Neon SQL Editor (see `app/cli.py`). A rejection sticks (the claimant is pointed to password reset, no new request); a decision counts for 30 days. The faster path for the real owner is password reset by email, which needs no approval. Confirmed, blocked or deleted accounts are never handed over (409 or "blocked").
- **Account enumeration:** registration, resend and forgot-password answer the same way for known and unknown addresses. Registration hashes the password in both branches to keep timing equal.
- **Cookie names (deviation in dev only):** staging and production use `__Host-mc_session` / `__Host-mc_csrf` / `__Host-mc_oauth` with Secure. Development and tests use `mc_session` etc. without the prefix or Secure, because the owner works on plain http://localhost. The switch is `Settings.secure_cookies`.
- **CSRF:** double-submit cookie. The page writes the token from `mc_csrf` into forms and into `hx-headers`; a POST passes only if the form field or `X-CSRF-Token` equals the cookie. It is checked per route by a test, so a new POST route without it fails CI. A new token is issued at sign-in.
- **Session rotation:** a new session ID and new CSRF token at every sign-in, Google sign-in and Google link. A password reset ends every existing session and issues none; the user signs in again. Sessions are compared by SHA-256 hash; idle 30 days (touched at most hourly), absolute 90 days.
- **Rate limits in Postgres:** fixed windows, keys hashed, rejected attempts still count. Sign-in: 5 attempts per email and 30 per IP in 15 minutes. Registration: 10 per IP and 3 emails per address per hour (over the address limit nothing is sent, the answer stays the same). Resend and reset: 3 per email and 20 per IP per hour; over the per-email limit both stay silent (nothing is sent, the answer is the same). Expired rows in `rate_limits`, `auth_tokens`, `oauth_flows` and stale `auth_sessions` are not purged yet (follow-up for the worker).
- **Client address for the IP limits:** set by `CLIENT_IP_HEADER`. Staging sets it to `CF-Connecting-IP` (Render serves through Cloudflare, which overwrites that header); unset, the connection address is used. `X-Forwarded-For` is never read directly: Render appends its own proxy address, so its last entry may be shared by every visitor and would lock out the whole site. The Cloudflare and Render behavior rests on community reports, not official docs. **Check after merge:** 31 sign-in POSTs to staging with different emails and forged `X-Forwarded-For` / `CF-Connecting-IP` must end in 429, and the owner must still sign in from another network right after; record the result here. **Result (2026-11-22, after PR #8):** 30 sign-in POSTs with different emails and a forged `X-Forwarded-For` got 401, the next ones 429, so the forged header is ignored and the per-IP limit holds. A forged `CF-Connecting-IP` never reaches the app: Cloudflare answers 403 (error 1000). While that network was still limited (429), the owner's sign-in attempt from a mobile network got the normal answer (401, wrong password), so the limit does not lock out other visitors. Check passed. **Production (VPS + Caddy, no Cloudflare):** leave `CLIENT_IP_HEADER` unset and set `FORWARDED_ALLOW_IPS` to Caddy's address, not `*`, or the first `X-Forwarded-For` entry can be forged.
- **Known trade-offs:** the 5-per-15-minutes limit per email lets a stranger keep a known user out of password sign-in (Google and reset still work). Resend and forgot-password take slightly longer for a known address (one extra insert); the per-address limit makes that impractical to measure.
- **One-time links:** using a confirmation or reset link also uses up every other open link of the same kind for that user, so an older email cannot be replayed. Using a link locks the user row before the link row, the same order as the Google handover, so the two cannot deadlock.
- **Commit before the response:** routes take the database session as `DbSession` (`Depends(get_db, scope="function")`), so the commit happens before the response is sent. With the default scope FastAPI commits afterwards: a sign-in redirect could arrive before its session row exists, and a failed commit would look like success. A test fails on any bare `Depends(get_db)`.
- **CSP `form-action`:** `'self' https://accounts.google.com`. "Link Google" is a POST form answered by a redirect to Google, and browsers check form-action against that redirect. Scripts and styles stay `'self'` only.
- **No caching:** every rendered page and every sign-in redirect sends `Cache-Control: no-store` (pages carry the CSRF token and the user's email).
- **Templates never get the ORM user:** `AccountView` (`app/schemas/account.py`) carries only name, email and yes/no flags.
- **Google:** at most 30 sign-in starts per IP in 15 minutes (each stores a flow row). The `id_token` check allows 60 seconds of clock skew. Authorization code with PKCE (S256), `state` bound to the browser by a cookie and single-use in Postgres, `nonce` checked, `id_token` checked against Google's JWKS with audience, issuer, expiry and `email_verified`. Token exchange and key fetch use the standard library, so no new HTTP dependency.
- **Google linking:** only from settings and only with POST and CSRF, and it shares the per-IP limit of Google sign-in starts (30 in 15 minutes). The callback must come from the same signed-in user who started it. A Google account already linked elsewhere, or whose email belongs to another account, is refused and never merged; the user is sent back to settings with a notice (`taken` or `email_taken`), like every other linking outcome.
- **Email:** one `EmailSender` interface. Development and staging write the message to the log, as the owner asked. This means one-time links appear in staging logs until a real provider is set up (PRODUCT_RULES A4). Production, until a provider is chosen, logs an error without the address or link and does not raise, so every page answers as usual (a 500 would reveal unconfirmed accounts). A provider must be set before launch.
- **Links in pages:** forms and in-site links use paths, not absolute URLs, so a wrong scheme behind the proxy cannot break them. Email links and the Google redirect URI are absolute and rely on uvicorn `--proxy-headers` reading `X-Forwarded-Proto`.
- **Language:** Russian by default and Tajik through `app/locales/{ru,tg}.json`. The owner reviewed the Tajik text (2026-10-25: OK). `account.linked_email_taken` (added 2026-11-22) reviewed by the owner on 2026-11-22: OK. A cookie `mc_lang` chooses the language; the switch returns to the same page. The Accept-Language header is not used yet.
- **Colors:** errors use the `danger` token (R-020).
- **Render variables:** `CLIENT_IP_HEADER=CF-Connecting-IP`, `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are listed in `render.yaml`, but a Blueprint change may not reach an existing service; the owner checks them in Render → Environment and adds what is missing (the Google pair is `sync: false`, which Render never creates on an existing service). Google redirect URI: `https://master-com-staging.onrender.com/auth/google/callback`.
- **Out of scope here:** Telegram sign-in (week 4), the admin block UI (week 10; `block_user` service exists and is tested), password change while signed in.

## R-020 Error colour (2026-10-25, owner approved)
- Variant 11 gets a `danger` token for form errors and error notices: `#B3261E` light, `#FF8A80` dark, with a `danger tint` background `#FDECEA` / `#3A1412`. All pairs pass WCAG AA (5.7 to 8.6). Invalid fields get a danger border, field errors danger text, error notices danger text on the tint.
- The brand yellow stays for emphasis; it reads as "attention", not "error".

## R-021 Telegram sign-in, linking and phone verification (2026-11-22, week 4; Claude's call, owner to confirm)
Implements BRIEF §4.1 (Telegram sign-in) and §4.2 (phone through the bot's "Share contact"). The owner asked Claude to decide; design: `docs/superpowers/specs/2026-11-22-telegram-signin-and-phone-design.md`. Every item below is **Claude's call, owner to confirm**:
- **D1. Bot, not the Telegram Login Widget:** the widget needs a script from `telegram.org` and an iframe from `oauth.telegram.org`, which break the `'self'`-only CSP exactly on the sign-in page. The bot is needed for the phone anyway.
- **D2. The bot hands out the sign-in link:** the site's "Sign in with Telegram" goes to `t.me/<bot>?start=login` with no token. The bot creates a one-time token for that Telegram user (10 minutes, stored hashed in `telegram_signins`) and sends a button to `/auth/telegram/<token>`. GET only shows "Sign in as <name>?"; the POST (CSRF) signs in with a new session, creating the account if needed (no email). The token only ever exists in the user's own chat, which avoids the "confirm this login in the bot" phishing pattern. Known cost: Telegram may open the link in its in-app browser, so the session lands there; the page says how to open it in the browser.
- **D3. Linking and phone start on the site:** on the account page an unlinked account has one POST button (CSRF) "Link Telegram and verify phone" that makes a one-time `tg_link` token (30 minutes) and redirects to `t.me/<bot>?start=link_<token>`; a linked account without a verified phone gets a plain link to `t.me/<bot>?start=phone` (no token). The bot first shows the masked account ("А*** · a***@gmail.com") with "Yes, it's my account" / "Cancel"; only "Yes" uses the token. A Telegram already linked to another account, or an account already linked to another Telegram, is refused and never merged. Right after linking (and on `/phone` or `/start phone`) the bot asks for the contact; only the user's own contact (`contact.user_id == from_user.id`) counts, normalised to E.164.
- **D4. Sessions:** Telegram sign-in rotates the session and CSRF token like Google. Linking in the bot touches no web session. Blocked or deleted accounts get nothing from the bot and the sign-in link is refused.
- **D5. Where the bot runs:** its own process (`python -m app.bot`, aiogram 3, the only async code; dialog logic is sync and called through `asyncio.to_thread`). Locally: compose service `bot`, long polling. Staging: a second free Render web service `master-com-bot-staging` from the same image in webhook mode (aiogram's aiohttp server, `POST /telegram/webhook` checked against `X-Telegram-Bot-Api-Secret-Token` with `hmac.compare_digest`, `GET /health` for Render). The image's entrypoint runs a given command instead of migrations, seed and uvicorn, so the bot never touches the schema. The web app never imports aiogram (a test checks it). The bot answers in private chats only, so a sign-in link can never land in a group. Production (week 12): decided then.
- **D6. Data:** `auth_tokens.purpose` gains `tg_link`; sign-in tokens get their own table `telegram_signins` because the account may not exist yet (it is created only by the browser POST). No table for bot state.
- **D7. Limits (Postgres):** bot sign-in links 5 per Telegram user per 15 minutes; bot link confirmations and contacts 20 per Telegram user per hour; site "Link Telegram" / "Verify phone" 10 per user per hour and 30 per IP per 15 minutes; POST `/auth/telegram/<token>` shares the sign-in per-IP limit (30 / 15 minutes). CSP `form-action` gains `https://t.me`.
- **D8. Settings and two bots:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_URL` (unset: polling), `SITE_URL` (base of the links the bot sends). **Amended 2026-11-22 (task review, owner to confirm):** the site needs only `TELEGRAM_BOT_USERNAME` to show the Telegram buttons; the bot token lives only on the bot service (least privilege), and the bot refuses to start without it. One bot per environment (development: polling from `.env`; staging `MasterComStagingBot`: webhook; production: a third bot), because Telegram refuses polling while a webhook is set.
- **D9. Texts:** `bot.*` and `telegram.*` in `app/locales/{ru,tg}.json`, chosen by the account locale, else Telegram's `language_code` (`tg` → Tajik, anything else → Russian). Drafted by Claude; owner approves (C9).
- **Refused confirmations use up the token:** when "Yes" is refused (`TELEGRAM_TAKEN`, `TELEGRAM_OTHER_LINKED`) the token is spent and the user asks for a new link on the site. Linking the same Telegram again answers "linked". If the account's phone is already verified, the bot does not ask for the contact after linking.
- **Deviation from D7:** the `/start link_...` preview is not rate limited (tokens are 256-bit, so guessing is pointless); only confirmations and contacts are (20 per Telegram user per hour).
- **Shared contact with an unusable number:** a number that does not normalise to a valid E.164 phone (`PHONE_INVALID`) gets the same answer as someone else's contact ("please use the button"); nothing is saved. Both answers send the contact button again (it is a one-time keyboard, so it hid itself after the first tap).
- **"Yes" button data:** the callback carries the token ID and an HMAC-SHA256 (keyed with `SECRET_KEY`, first 16 hex characters) over token ID + Telegram ID, so a button shown to one Telegram user is refused for another and the button carries no raw token. Only the bot process creates and checks this data, so the bot service has its own `SECRET_KEY` (Render generates it); it need not equal the site's.
- **A verified phone cannot be changed in the bot:** sharing a different phone answers "contact support"; sharing the same phone again answers "already verified". A verified phone is what makes a master, so changing it is a support (later admin) action in v1. Unlinking Telegram is also a support action.
- **Two bots, dev and staging:** see D8; production gets its own bot in week 12.
- **Polling refused on staging and production:** with `APP_ENV` `staging` or `production` and no `TELEGRAM_WEBHOOK_URL`, the bot exits with a message before any call to Telegram, so a staging token on a laptop cannot drop the staging webhook. Webhook mode also refuses to start without `TELEGRAM_WEBHOOK_SECRET`.
- **Logs:** the bot logs update types and outcomes only; failed updates log the exception type and stack frames, never the message (a database error can quote a phone). Tokens, phone numbers and the bot token are never logged.
- **`/start` handler:** `CommandStart()`, not `CommandStart(deep_link=True)`, so a bare `/start` gets the welcome text instead of falling through to help. `/phone` is the same as `/start phone`. Callback buttons are always answered, even when handling fails.
- **New dependency (reason for the PR):** `aiogram` 3.31 (brings `aiohttp`), the bot framework named in the stack (R-003), and its aiohttp server for the webhook. aiogram requires `pydantic<2.14`, which forces `pydantic` down from 2.14.0 to 2.13.5 (`pydantic-core` 2.50.0 → 2.46.5); the full test suite passes on it.

## R-022 Master profiles, portfolio and uploads (2026-11-23, weeks 5–6; Claude's calls, owner to confirm)
Implements BRIEF §4.2–§4.6, §4.13 and PRODUCT_RULES C1, C2. Design: `docs/superpowers/specs/2026-11-23-master-onboarding-profile-portfolio-design.md`. Delivered in two PRs: **PR A** (D1–D8, D10 profile, D11) and **PR B** (D9 portfolio management, onboarding step 5, the public collection grid and gallery page). Every item is **Claude's call, owner to confirm**:
- **D1. Who is a master:** a user who owns a `master_profiles` row (`origin='self'`); no role column. Creating the profile rotates the session (R-011).
- **D2. Onboarding:** `/master/onboarding/basics|specialties|area|phone`, one server-rendered step per page, each saved on submit; basics creates the draft profile. Step 5 (first collection, optional, skippable) is at `/master/onboarding/collection`.
- **D3. Publishing (C1):** `published_at` is set only with a verified phone, at least 1 specialty, at least 1 area, not hidden and an active owner; it is re-checked after every edit and cleared when a condition stops holding. Drafts are visible only to the owner (404 to others). Photos are optional.
- **D4. Slug (C2):** transliterated from the display name (Russian plus Tajik letters), lowercase, `[a-z0-9-]`, up to 40 characters, minimum 3, `-2`, `-3` on a clash; editable, with a live HTMX check. Uniqueness is final at save (friendly error, never 500). No redirect from an old slug in v1 (no history table).
- **D5. Contacts:** the contact phone is always the verified phone, never typed. Telegram username, WhatsApp number and Instagram handle are optional. Contacts are rendered only for signed-in users; the guest HTML does not contain them.
- **D6. Plan limits** from config (`PLAN_LIMITS`): 30 collections, 60 photos per collection, 10 specialties, 3 primary, 20 areas (the areas limit is a guess).
- **D7. Upload pipeline** (`app/services/images.py`): JPEG, PNG and WebP by magic bytes only, up to 10 MB checked while reading, at most 40 megapixels, EXIF orientation applied, then re-encoded as WebP so all metadata is dropped; originals are never stored. Portfolio images 1600 px plus a 400 px thumbnail; avatar a 400 px centre-cropped square. Storage keys are random and server-made.
- **D8. Storage and serving:** a `Storage` interface with `LocalDiskStorage` (`MEDIA_ROOT`, atomic writes), served at `/media/{key}` with a strict key regex, immutable caching and `nosniff`. R2 is a later drop-in. `UPLOADS_ENABLED` is true in development and test and false on staging and production until R2 exists (Render's free disk is wiped on every deploy); when false, upload controls are hidden and upload POSTs answer 404.
- **D9. Portfolio management** (`/master/portfolio`, as built): limits come from `PLAN_LIMITS` (30 collections, 60 live photos per collection; the count is taken under a row lock on the collection, so two uploads at the limit cannot pass together). Up to 10 files per upload request, with a larger body cap for that route (10 files x 10 MB plus form overhead; other routes keep 64 KiB); each file is processed on its own, bad files are listed and the good ones are saved. Deleting a photo is a soft delete with "Undo" for 30 days; deleted photos are excluded everywhere and do not count toward the limit (a purge job for older ones comes later). Photo and collection positions are ordered but may have gaps after deletes; moves swap neighbours. Collections can be hidden and reordered. Upload controls are off when `UPLOADS_ENABLED` is false (staging by default), uploads then answer 404.
- **Public collections:** the profile shows a grid of square tiles (cover = first visible photo, title, photo count) for collections that are not hidden and have at least one visible photo, plus a "Manage portfolio" link for the owner. `/m/<slug>/c/<id>` is the gallery: a horizontal scroll-snap strip of full photos (lazy, with width and height), a thumbnail row linking to `#photo-<n>`, a counter "n / total" from a small static Alpine CSP component (`assets/js/gallery.js`, copied to `app/static/js` by `scripts/vendor.mjs` and loaded only on this page), the description and the master's card. Without JS the counter reads "1 / N" and thumbnails still jump to the photos. A hidden collection, one with no visible photos, or one of a draft or hidden master answers 404 to everyone but the owner; the owner also sees moderation-hidden photos. The page is `no-store`; a draft master's collection also gets `noindex`.
- **D11. Security:** every write loads the master by the current user, never by an ID from the URL; CSRF on every POST; upload tests for wrong magic bytes, size limit, decompression bomb, EXIF removal and orientation; `/media/` refuses `..` and anything off the key regex.

Decisions taken during the build:
- **Pillow 13.0.0 through a per-package `exclude-newer` exception:** pip-audit reports 42 advisories for 12.3.0, the newest version inside the project's date cutoff, so 13.0.0 is pinned on purpose.
- **Request body cap before parsing** (`app/core/body_limit.py`): 64 KiB for normal POSTs, `MAX_UPLOAD_BYTES` + 64 KiB on upload paths; bigger bodies get 413 (chunked or lying bodies are cut off while reading; FastAPI may answer 400), so a huge body is never buffered.
- **`auth.is_active` is public** (renamed from private) because the profile services need the same "active account" test.
- **Services validate lengths and ranges themselves** (`MasterError` codes), so a database CHECK error never turns into a 500.
- **Animated images keep the first frame, and any unreadable image gives a friendly error,** never a traceback.
- **A district whose city is selected as a whole is dropped,** so an area is never listed twice.
- **The contact phone is always the verified phone; WhatsApp is normalised like the bot's phone** (`normalize_phone`), so both use one format.
- **Profile pages send `Cache-Control: no-store`; drafts also send `noindex`,** so a draft is neither cached nor indexed.
- **R2 adapter (D8, as built):** `R2Storage` in `app/services/storage.py` implements the same `Storage` protocol through the S3 API (`put_object` with `Cache-Control: public, max-age=31536000, immutable`, `delete_object`, URL = `R2_PUBLIC_URL/<key>`); it is used when all five `R2_*` settings are set, otherwise local disk. Both URLs must be `https://` (checked at startup; settings errors never echo values). With R2 on, the CSP `img-src` adds exactly the public bucket origin and `/media/` answers 404. Backend failures become `StorageError` and a friendly "could not save" message, never a 500. `boto3` is the dependency: the standard S3-compatible client, pre-approved by the owner 2026-11-23; `moto[s3]` (dev only) fakes S3 in tests. `UPLOADS_ENABLED` stays false on staging until the owner turns it on after the deploy. The `/media/` key regex now accepts ASCII digits only.
- **Note for week 7:** search must list only published profiles whose owner is active. `published_at` stays set when an owner is blocked, and `public_master` re-checks the owner at read time; search has to do the same.

## R-023 Working name and translation review (2026-11-23, owner)
- **Working name: Ustoiman** (owner, 2026-11-23: "назовем пока"). It replaces "Master COM" as the working name; the final name and the `.tj` domain are still open. The site title and texts switch once the owner confirms the Cyrillic spelling (proposed: «Устоиман»); code identifiers and repository names stay as they are.
- **Tajik texts are reviewed in bulk at the end** (owner): `docs/TRANSLATIONS.md` lists every text in ru and tg with a status, generated from the locale files; a test keeps it current. Texts the owner checked are recorded in `app/locales/tg_approved.json`; the week-3 texts (reviewed 2026-10-25 and 2026-11-22) start as approved, everything newer as draft.

## Open
- Final name and positioning (round 6): working name **Ustoiman** (R-023); final name and domain still open.
- Visual direction: tentative pick in R-014; final confirmation pending.
- Logo designer: postponed by the owner.
- n8n: not in v1; candidate for post-launch internal ops automation.
