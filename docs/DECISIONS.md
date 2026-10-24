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
- **Confirmation before password sign-in:** an unconfirmed account cannot sign in, and its answer is the same 401 and text as a wrong password ("wrong email or password; if you just registered, confirm your email first"). With the right password it also quietly gets a fresh confirmation link (at most 3 per address per hour, shared with "resend"). A separate "confirm your email" answer would let anyone test an address by registering it and then signing in. Confirmation links are single-use and expire after 48 hours; reset links expire after 1 hour and also confirm the address.
- **Account enumeration:** registration, resend and forgot-password answer the same way for known and unknown addresses. Registration hashes the password in both branches to keep timing equal.
- **Cookie names (deviation in dev only):** staging and production use `__Host-mc_session` / `__Host-mc_csrf` / `__Host-mc_oauth` with Secure. Development and tests use `mc_session` etc. without the prefix or Secure, because the owner works on plain http://localhost. The switch is `Settings.secure_cookies`.
- **CSRF:** double-submit cookie. The page writes the token from `mc_csrf` into forms and into `hx-headers`; a POST passes only if the form field or `X-CSRF-Token` equals the cookie. It is checked per route by a test, so a new POST route without it fails CI. A new token is issued at sign-in.
- **Session rotation:** a new session ID and new CSRF token at every sign-in, Google sign-in and Google link. A password reset ends every existing session and issues none; the user signs in again. Sessions are compared by SHA-256 hash; idle 30 days (touched at most hourly), absolute 90 days.
- **Rate limits in Postgres:** fixed windows, keys hashed, rejected attempts still count. Sign-in: 5 attempts per email and 30 per IP in 15 minutes. Registration: 10 per IP and 3 emails per address per hour (over the address limit nothing is sent, the answer stays the same). Resend and reset: 3 per email and 20 per IP per hour. Expired rows in `rate_limits`, `auth_tokens`, `oauth_flows` and stale `auth_sessions` are not purged yet (follow-up for the worker).
- **Client address for the IP limits:** set by `CLIENT_IP_HEADER`. Staging sets it to `CF-Connecting-IP` (Render serves through Cloudflare, which overwrites that header); unset, the connection address is used. `X-Forwarded-For` is never read directly: Render appends its own proxy address, so its last entry may be shared by every visitor and would lock out the whole site. The Cloudflare and Render behavior rests on community reports, not official docs. **Check after merge:** 31 sign-in POSTs to staging with different emails and forged `X-Forwarded-For` / `CF-Connecting-IP` must end in 429, and the owner must still sign in from another network right after; record the result here. **Production (VPS + Caddy, no Cloudflare):** leave `CLIENT_IP_HEADER` unset and set `FORWARDED_ALLOW_IPS` to Caddy's address, not `*`, or the first `X-Forwarded-For` entry can be forged.
- **Known trade-offs:** the 5-per-15-minutes limit per email lets a stranger keep a known user out of password sign-in (Google and reset still work). Resend and forgot-password take slightly longer for a known address (one extra insert); the per-address limit makes that impractical to measure.
- **One-time links:** using a confirmation or reset link also uses up every other open link of the same kind for that user, so an older email cannot be replayed.
- **Commit before the response:** routes take the database session as `DbSession` (`Depends(get_db, scope="function")`), so the commit happens before the response is sent. With the default scope FastAPI commits afterwards: a sign-in redirect could arrive before its session row exists, and a failed commit would look like success. A test fails on any bare `Depends(get_db)`.
- **CSP `form-action`:** `'self' https://accounts.google.com`. "Link Google" is a POST form answered by a redirect to Google, and browsers check form-action against that redirect. Scripts and styles stay `'self'` only.
- **No caching:** every rendered page and every sign-in redirect sends `Cache-Control: no-store` (pages carry the CSRF token and the user's email).
- **Templates never get the ORM user:** `AccountView` (`app/schemas/account.py`) carries only name, email and yes/no flags.
- **Google:** authorization code with PKCE (S256), `state` bound to the browser by a cookie and single-use in Postgres, `nonce` checked, `id_token` checked against Google's JWKS with audience, issuer, expiry and `email_verified`. Token exchange and key fetch use the standard library, so no new HTTP dependency.
- **Google linking:** only from settings and only with POST and CSRF. The callback must come from the same signed-in user who started it. A Google email that matches another account is refused (409), never merged.
- **Email:** one `EmailSender` interface. Development and staging write the message to the log, as the owner asked. This means one-time links appear in staging logs until a real provider is set up (PRODUCT_RULES A4). Production refuses to send, so nothing is lost silently.
- **Language:** Russian by default and Tajik through `app/locales/{ru,tg}.json`. The Tajik text is a first draft and needs the owner's proofreading. A cookie `mc_lang` chooses the language; the switch returns to the same page. The Accept-Language header is not used yet.
- **Colors:** `design_tokens.md` has no error color. Error messages use bold text, a 2px border and `aria-invalid`, not red. Proposal: add a `danger` token to variant 11 when the owner picks the colour.
- **Render variables:** `CLIENT_IP_HEADER=CF-Connecting-IP`, `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are listed in `render.yaml`, but a Blueprint change may not reach an existing service; the owner checks them in Render → Environment and adds what is missing (the Google pair is `sync: false`, which Render never creates on an existing service). Google redirect URI: `https://master-com-staging.onrender.com/auth/google/callback`.
- **Out of scope here:** Telegram sign-in (week 4), the admin block UI (week 10; `block_user` service exists and is tested), password change while signed in.

## Open
- Final name and positioning (round 6): postponed by the owner; working name Master COM stays.
- Visual direction: tentative pick in R-014; final confirmation pending.
- Logo designer: postponed by the owner.
- n8n: not in v1; candidate for post-launch internal ops automation.
