# Weeks 8–9: requests, responses, lifecycle, notifications and the worker

Status: design, 2026-11-26. The owner handed product questions to Claude (same mode as weeks 5–7); choices
not quoted from BRIEF, PRODUCT_RULES or DECISIONS are **Claude's calls**, logged as R-026 for the owner to
confirm.

## Why

BRIEF §10 weeks 8–9: "Requests, responses, lifecycle, notifications, worker — done when the full request flow
works". This is the marketplace itself: a client posts a job, matching masters see it and respond, the client
chooses one, the job gets done. Notifications bring both sides back (most users live in Telegram).

## What exists

- Tables from week 2: `requests` (`JobRequest`: client, city/district, description, budget amount+unit,
  timing+date, hidden `address`, status `open|master_chosen|completed|closed|cancelled`, `chosen_master_id`,
  `responses_count`, `feed_paused_at`, `completed_at`, `closed_at`, `hidden_at`; CHECKs for consistency),
  `request_specialties`, `request_photos` (≤ 4, positions 0–3), `responses` (`JobResponse`: master, message ≤ 500,
  price amount+unit; unique per request+master). `PRICE_UNITS = fixed, m2, running_m, piece, hour` (C3).
- Masters: profiles, specialties, areas, publication rule (R-022); search index (R-025).
- Uploads pipeline + R2 storage (R-022); body caps; CSRF; sessions.
- Telegram: users link Telegram (`users.telegram_id`); bot process with aiogram (R-021); the web app may call the
  Bot API with plain sync HTTP (R-003). Email: `EmailSender` interface, log-only until a provider is chosen (A4).
- Sentry (R-024). No jobs table, no notification preferences yet.

## Goals

1. **Post a request** (BRIEF §4.8): any signed-in user; specialty (1–3), city (+ district when the city has
   districts), description (20–2000 chars), up to 4 photos, optional budget (amount + unit, C3), timing
   (urgent / date / flexible), exact address (optional, hidden). Editable while `open` and before the first
   response; after that, editing is locked (my call: masters must not respond to a job that later changes).
2. **Client's requests**: list and detail pages; responses listed on the detail page with each master's card
   (contacts per §4.6: a client sees a master's contacts immediately); actions: choose a master, mark completed,
   close, cancel, reopen to the feed after the 15-response pause.
3. **Masters' feed** (§4.8): open, not hidden, not paused requests matching the master's specialties AND work
   area (district rule like search: request district ∈ master districts, or master serves the whole city), not
   their own, newest first, cursor pagination; only published masters (C1) see the feed; a master sees a
   request's description, place, budget, timing and photos — never the address, never the client's contacts
   until chosen (§4.6).
4. **Respond**: "Ready to take it" + optional message (≤ 500) + optional price (amount + unit); one per master per
   request, never on own request, only while `open` and not paused; a master can withdraw before being chosen.
   After 15 responses the request leaves the feed (`feed_paused_at`); the client can reopen it.
5. **Lifecycle**: `open → master_chosen → completed`; `closed`/`cancelled` from any state before `completed`;
   choosing a master: only a responder; after choosing, the chosen master sees the client's contacts; others
   see "another master was chosen". Completed → a review prompt (reviews themselves are week 10).
6. **Notifications** (§4.10) via the Telegram bot (if linked) and email (if confirmed; log-only until A4), sent by
   the worker, never inside the web request: new matching request (to masters), new response (to client),
   "you were chosen" (to master), request completed — please leave a review (to client). Chat-message
   notifications come with chat (week 10). Each user can switch each event off per channel.
7. **Periodic rules**: C4 — open requests auto-close after 30 days, reminder to the client 3 days before; C5 —
   `master_chosen` for 14 days → ask the client "Is the job done?"; after 30 days → close without review.
8. **Worker**: Postgres jobs table + `SELECT … FOR UPDATE SKIP LOCKED` (§6.3), retries with backoff, idempotent
   handlers, a periodic scheduler for C4/C5 and the purge of expired auth rows (R-019 follow-up).

## Non-goals

- Chat, "send address", reviews, complaints (week 10). Web push (v1.1). Paid plans.
- Real email delivery (A4 provider still open): email notifications go through `EmailSender` (log-only).
- Request search/SEO for requests (requests are private-ish: listed only in masters' feeds).
- Imported profiles responding (they cannot, R-016).

## Anti-goals

- A master ever seeing a request's exact address, or a client's phone/Telegram before being chosen.
- A user acting on someone else's request or response by changing an ID.
- A notification sent twice for the same event, or sent inside the web request (slow pages, lost on errors).
- Notifications to a blocked user, or a Telegram message with personal data of the other side beyond what the
  page would show them.
- The worker taking down the site (crash loops) or holding row locks for long.

## Decisions (Claude's calls)

### D1. Who can do what
- Post: signed-in, active user (masters too, as clients). Phone verification not required for clients (§4.2).
- Respond: published master (C1) whose owner is active; not on own request.
- Every write goes through a service that loads the request/response by id **scoped to the current user**
  (client_id or master's user_id); foreign ids → 404.

### D2. Request form and editing
- Specialties 1–3 (BRIEF says "one or more"; 3 caps noise). City required; district required only when the city
  has districts (R-018). Description 20–2000 chars. Budget optional; amount > 0 with unit, or neither. Timing date
  must be today..+180 days. Address optional ≤ 300 chars.
- Photos: same pipeline (`process_portfolio`), stored under `r/<yyyy>/<mm>/<hex>.webp` (+ `-t`), ≤ 4; upload
  route gets the multi-file body cap (4 files); uploads off → photo inputs hidden, the rest works.
- Editing allowed only while `open` and `responses_count == 0`; afterwards the client can only close/cancel.

### D3. Feed matching and the 15 limit
- Match: `request.specialty ∈ master.specialty_ids` AND (`request.district ∈ master.district_ids` OR
  `request.city ∈ master.whole_city_ids` OR (request has no district AND `request.city ∈ master.city_ids`)) —
  reuse `master_search` arrays.
- Feed shows `open`, not hidden, `feed_paused_at IS NULL`, not own, not already responded (responded ones are in
  "My responses"). Cursor = (created_at, id) descending, 20 per page.
- The 15th response sets `feed_paused_at` in the same transaction (row lock on the request); the client's
  "Reopen" clears it (responses_count stays; next pause after 15 more — my call: pause again at every +15).

### D4. Contacts and address (§4.6)
- Client sees responders' contacts (same contact view as profiles, signed-in).
- Master sees the client's name only; after being chosen: client's verified phone (if any) and Telegram link
  (if linked) — through a dedicated view model; never the email. Address: never shown in weeks 8–9 (chat's
  "Send address" in week 10 will release it).

### D5. Lifecycle rules
- choose(master): request `open`, master has a non-withdrawn response → `master_chosen`, `chosen_master_id`.
- complete: `master_chosen` → `completed`, `completed_at`; review prompt notification.
- close (client "no longer needed") / cancel ("posted by mistake"): any state before `completed` → `closed` /
  `cancelled`, `closed_at`. (Both close the request; the reason differs for analytics.)
- unchoose is not offered in v1 (my call): the client closes and reposts if the master fails.
- withdraw (master): only while the request is `open` or the master is not the chosen one → response deleted
  (soft: `withdrawn_at`), counts decrement; never after being chosen.
- Every transition locks the request row (`FOR UPDATE`) and checks the expected state; a stale double-click is a
  friendly "already done", never a 500.

### D6. Jobs table and worker
- Table `jobs`: `id`, `kind` (string), `payload` jsonb (ids only, never personal data), `run_at`, `attempts`,
  `max_attempts` (default 5), `locked_at`, `locked_by`, `done_at`, `failed_at`, `last_error` (exception type
  only), `dedupe_key` unique nullable (e.g. `notify:new_response:<response_id>:<user_id>:<channel>`).
- Enqueue inside the same transaction as the business change (so a rolled-back change sends nothing).
- Worker loop: claim up to 10 due jobs with `FOR UPDATE SKIP LOCKED`, commit the claim (`locked_at`), run each
  handler in its own transaction, mark done or schedule a retry (`run_at = now + 2^attempts minutes`), give up
  after `max_attempts` (`failed_at`). Stale locks older than 10 min are re-claimable.
- Periodic scheduler inside the worker: every 10 minutes enqueue `periodic:*` jobs with dedupe keys per window
  (C4 reminders/auto-close, C5 ask/close, purge expired auth rows).
- Handlers re-check state at run time (e.g. skip "you were chosen" if the request was cancelled meanwhile; skip
  blocked users).

### D7. Where the worker runs
- Code: `python -m app.worker` (sync, no aiogram).
- **Production (week 12 VPS):** its own container in compose.
- **Staging (Render free has no background workers):** the worker loop runs as a background **thread inside the
  bot service** process (`RUN_WORKER=1` on the bot service only). Reason: the bot service is the one that already
  holds the Telegram bot token (R-021 least privilege), already talks to the database, and the web app must not
  get the token. When the web app enqueues a job it fires a best-effort wake-up `GET` to the bot service's
  `/health` (1 s timeout, in a background thread, failures ignored) so a sleeping free service wakes up; while it
  sleeps, jobs wait (acceptable on staging; documented). Locally: a `worker` compose service (profile `worker`).
- The thread is a daemon with its own DB sessions; crashes are logged to Sentry and the loop restarts after a
  pause; the bot keeps working if the worker fails.

### D8. Notifications
- Per user and event, preferences in `notification_prefs (user_id, event, channel, enabled)`; default enabled for
  all events on Telegram (if linked) and email (if confirmed). A settings page section lists events × channels.
- Telegram: worker sends with the Bot API `sendMessage` (sync HTTP, `TELEGRAM_BOT_TOKEN` from the worker's env),
  short texts in the recipient's locale with a link to the page; no personal data of the other side (no phone,
  no address; the client's first name is fine). If Telegram answers 403 (user blocked the bot) → mark the
  channel off for that user and stop.
- Email: `EmailSender` (log-only until A4) with the same texts.
- New-request fan-out: one `notify:new_request` job per request; the handler finds matching published masters
  (D3 rule) and enqueues per-master notifications with dedupe keys, capped at 200 masters per request (newest
  active first) to bound bursts.
- Rate: at most 20 new-request notifications per master per day (my call), the rest are skipped (they still see
  the feed).

### D9. Pages and texts
- Client: "New request" (from the home page CTA and SEO pages' "post a request"), "My requests", request detail
  with responses. Master: "Feed", "My responses". Account: notification settings.
- All texts in ru/tg locales; TRANSLATIONS.md regenerated. Mobile first.
- The SEO pages' "post a request" link (R-025) now points to the new-request page with city and specialty
  prefilled.

### D10. Answers to the builder check (Claude's calls)
1. **How the chosen master reaches the client:** choosing a master asks the client for a **contact phone**
   (prefilled with their verified phone if they have one; normalized E.164, required). It is stored on the request
   (`client_phone`) and shown only to the chosen master, with a `tel:` link. If the client has linked Telegram, the
   master's "you were chosen" message also says the client can be reached through UstoiMan chat later (week 10).
   No email, no Telegram id is exposed.
2. **Withdraw and respond again:** allowed while the request is `open`: re-responding updates the same row (clears
   `withdrawn_at`, new message/price). `responses_count` = active (non-withdrawn) responses. The pause uses a stored
   threshold `feed_pause_at` (15 at creation): when active responses reach it, set `feed_paused_at`; "Reopen" clears
   `feed_paused_at` and sets `feed_pause_at = responses_count + 15`. Withdrawing never auto-unpauses. Withdrawn
   responses are not shown to the client.
3. **Events:** other responders get **no notification** when another master is chosen (page state "another master
   was chosen" only). The chosen master **is** notified when the request is closed/cancelled after choosing (new
   event `request_cancelled`). No event for responders on close before choosing.
   Final events: `new_request` (masters), `new_response` (client), `chosen` (master), `request_cancelled` (chosen
   master), `review_prompt` (client), `expiry_reminder` (client, C4), `done_check` (client, C5).
4. **Clocks:** new columns `opened_at` (set at creation and on every reopen), `master_chosen_at`, `close_reason`
   (`client_closed`, `client_cancelled`, `auto_expired`, `auto_stale`), `expiry_reminded_at`, `done_asked_at`. C4 applies
   to status `open` (paused or not), measured from `opened_at`: reminder at +27 days, auto-close at +30 (`closed`,
   reason `auto_expired`). C5 from `master_chosen_at`: ask at +14 days, close at +30 (`closed`, reason `auto_stale`,
   no review prompt, chosen master not notified).
5. **Master eligibility:** feed and responding use the same visibility as search (`master_search.is_visible` AND the
   owner-active re-check). Existing responses stay when a master unpublishes; responses whose master's owner is
   blocked/deleted are hidden from the client. The new-request fan-out uses the same visibility.
6. **Telegram delivery state:** `users.telegram_id` is the private chat id. New column `users.telegram_blocked_at`:
   set when the Bot API answers 403; cleared when the user sends anything to the bot (dialog layer). Preferences stay
   the user's own choice; the settings page shows "bot blocked — open the bot and press Start" while it is set.
7. **Staging timers:** the periodic scheduler only ticks while the bot service is awake (woken by enqueue pings and
   Telegram updates). Accepted for staging; documented. A CLI `python -m app.cli worker run-once` runs due jobs and
   the periodic enqueue once (local testing).
8. **Delivery semantics:** at-least-once with a guard: retry only when the message certainly was not delivered
   (connection refused/DNS, HTTP 429 with retry_after, HTTP 5xx before a response body); a read timeout after sending
   is treated as delivered (no retry). Dedupe keys prevent double enqueue.
9. **Visibility of a request page:** the client always; masters while it is `open` and matches their feed; every
   master who responded keeps it in "My responses" with a status (open / another master chosen / you were chosen /
   closed / cancelled). Hidden by admin or client deleted → responders see "This request is no longer available"
   without details; everyone else 404.

## Constraints
- CLAUDE.md: sync FastAPI, services own rules, CSRF everywhere, no |safe/inline script, someone-else-tries tests
  for every write route, friendly errors never 500, migrations reviewed and reversible.
- No new Python dependencies expected (HTTP to Telegram via the standard library or the existing httpx in tests
  only — check what the project already uses for Google token exchange: urllib).

## Delivery (three PRs)
- **PR A — Requests:** migration (jobs, notification_prefs, request columns from D10 (`opened_at`, `master_chosen_at`, `close_reason`, `expiry_reminded_at`, `done_asked_at`, `client_phone`, `feed_pause_at`), response `withdrawn_at`, `users.telegram_blocked_at`),
  request service + pages (post/edit/list/detail/close/cancel/reopen), photos.
- **PR B — Feed, responses, lifecycle:** feed, respond/withdraw, choose/complete, contacts rules, "my responses".
- **PR C — Worker and notifications:** jobs + worker loop + periodic C4/C5 + purge, notification prefs page,
  Telegram/email senders, enqueue points from A/B, staging wiring (bot service thread + wake-up ping), R-026.

## Left to the builder
- Exact page layouts within design tokens; copy texts (ru; tg drafts).
- Backoff details, worker poll interval (default 5 s), log formats.
