# Requests, responses, lifecycle, notifications and the worker: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clients post requests, matching masters see them in a feed and respond, the client chooses a master and completes the job; the worker sends notifications and runs the C4/C5 timers.

**Architecture:** Sync services own every rule (`app/services/requests.py`, `responses.py`, `jobs.py`, `notifications.py`); routers in `app/web/requests.py`, `app/web/feed.py`, `app/web/notifications.py`; the worker `app/worker/` is plain sync Python run as its own process (production, local compose) or as a thread inside the bot service (staging).

**Tech Stack:** FastAPI (sync), SQLAlchemy 2.0, Postgres (`FOR UPDATE SKIP LOCKED`), Jinja2 + HTMX, Telegram Bot API over stdlib HTTP, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-26-requests-responses-notifications-design.md`

## Global Constraints

- Every write loads the request/response scoped to the current user (client_id, or the master's user_id); foreign ids → 404; "someone else tries" test per write route; CSRF on every POST.
- A master never sees a request's `address` (weeks 8–9) and sees the client's `client_phone` only after being chosen; the client's email is never shown to masters.
- Request fields: specialties 1–3, city (+ district iff the city has districts), description 20–2000, budget amount > 0 with unit (`fixed, m2, running_m, piece, hour`) or neither, timing `urgent|date|flexible` (date within today..+180 days), address ≤ 300 (optional), photos ≤ 4 (multi-file cap 4 files). Editable only while `open` and `responses_count == 0`.
- Feed/responding visibility = search visibility (`master_search.is_visible` AND owner active re-check). Feed = open, not hidden, not paused, matching (spec D3), not own, not already responded; cursor (created_at, id) desc, 20 per page.
- Pause: active responses ≥ `feed_pause_at` (15 initially) → `feed_paused_at`; reopen → `feed_pause_at = responses_count + 15`. One response per master per request; re-respond after withdraw updates the same row.
- Transitions lock the request row and check the expected state; stale double-clicks → friendly message, never 500.
- Jobs: enqueued in the same transaction as the change; payload ids only; dedupe keys unique; retries with exponential backoff, max 5; stale locks (> 10 min) reclaimable.
- Notification events: `new_request, new_response, chosen, request_cancelled, review_prompt, expiry_reminder, done_check`; channels `telegram, email`; default on; per-user-per-event-per-channel opt-out; no personal data of the other side in messages; ≤ 20 new-request notifications per master per day; fan-out ≤ 200 masters per request.
- C4: from `opened_at`, reminder +27 d, auto-close +30 d (`closed`, `auto_expired`); C5: from `master_chosen_at`, ask +14 d, close +30 d (`closed`, `auto_stale`, no review prompt).
- Telegram: `users.telegram_id` is the chat id; 403 → `users.telegram_blocked_at`; cleared when the user writes to the bot. Retry only when not delivered (connect error, 429, 5xx); read timeout = delivered.
- All texts via ru/tg locales; regenerate `docs/TRANSLATIONS.md`. Checks before every commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -o addopts=""` (+ `uv run alembic check`).
- Three PRs: **A = Tasks 1–3** (`week-8-requests`), **B = Tasks 4–6** (`week-8-feed`), **C = Tasks 7–9** (`week-9-worker`).

## Review Focus

1. **A master opening a request by id** that is not in their feed and they did not respond to → 404 (no leak of description/photos). Test in Task 3/6.
2. **Double submit of "choose" or "respond"** (two tabs) → one effect, friendly message on the second. Tests in Tasks 4–5.
3. **A blocked client or master** mid-flow → their requests/responses disappear from others' views; no notifications to them. Tests in Tasks 5 and 8.
4. **Worker crash in a handler** → the job retries, other jobs continue, the bot keeps working (staging thread). Test in Task 7/9.
5. **Timezone edges for C4/C5** (UTC storage, day counts) → no premature close. Test in Task 7.

---

### Task 1: Migration and models
**Files:** `app/models/request.py`, `app/models/user.py`, new `app/models/jobs.py` (`Job`, `NotificationPref`), `app/models/__init__.py`, migration `0007_requests_jobs`, `docs/DATA_MODEL.md`. **Test:** `tests/test_constraints.py`.
- Request columns: `opened_at` (not null, default now), `master_chosen_at`, `close_reason` (CHECK one of `client_closed, client_cancelled, auto_expired, auto_stale`, set iff status closed/cancelled), `expiry_reminded_at`, `done_asked_at`, `client_phone` (E.164 CHECK `phone_ok`, required when status ∈ master_chosen/completed — CHECK), `feed_pause_at` int default 15 > 0; `master_chosen_at` set iff chosen_master_id set. Response: `withdrawn_at`. User: `telegram_blocked_at`.
- `jobs` and `notification_prefs` per spec D6/D8 (`notification_prefs` PK (user_id, event, channel), CHECK on event/channel values).
- Indexes: jobs `(run_at) WHERE done_at IS NULL AND failed_at IS NULL`; requests `(status, opened_at)`, `(status, master_chosen_at)`.
- [ ] RED constraint tests for each new CHECK/unique → implement → `alembic upgrade/check/downgrade -1/upgrade` → full checks → commit `feat(requests): data model for requests lifecycle, jobs and notification prefs`.

### Task 2: Request service
**Files:** `app/services/requests.py`, `app/schemas/requests.py`. **Test:** `tests/test_requests_service.py`.
- `RequestInput` (pydantic, constraints above); `create_request(db, user, data) -> JobRequest`; `update_request(db, user, request_id, data)` (only open & 0 responses); `add_request_photos(db, storage, user, request_id, files_bytes) -> list[errors]` (≤ 4, pipeline `process_portfolio`, keys `r/<yyyy>/<mm>/<hex>.webp`; extend `KEY_RE` and `new_key("r")`); `delete_request_photo`; `close_request(db, user, request_id, reason: Literal["closed","cancelled"])`; `reopen_feed(db, user, request_id)`; `client_requests(db, user) -> list[RequestCard]`; `request_for_client(db, user, request_id) -> RequestView`. Errors `RequestError(code)`.
- [ ] RED tests: validation codes; district required iff city has districts; edit locked after a response (simulate by inserting one); close/cancel from each state; reopen math (`feed_pause_at`); foreign ids → not_found for every function; photo limit and cleanup on DB failure; blocked user cannot create → implement → full checks → commit `feat(requests): request service`.

### Task 3: Request pages
**Files:** `app/web/requests.py`, templates `pages/requests/new.html`, `mine.html`, `detail_client.html`; `app/core/body_limit.py` (request photo path, cap 4 files); home CTA + SEO "post a request" link with `?c=&s=` prefill; locales. **Test:** `tests/test_requests_web.py`.
- Routes: `GET/POST /requests/new`, `GET /requests/mine`, `GET /requests/{id}` (client view in PR A; master views added in Task 6), `POST /requests/{id}/edit`, `/photos`, `/photos/{pid}/delete`, `/close`, `/cancel`, `/reopen`.
- [ ] RED tests: guest → sign-in redirect; create flow with prefill; uploads off → photo inputs hidden and 404 on photo POST; someone-else-tries for every POST; master who is not the client → 404 on `/requests/{id}` (until Task 6 adds the master view); address never rendered to anyone but the client → implement → full checks → commit `feat(requests): post and manage requests`.

### Task 4: Feed and responding
**Files:** `app/services/responses.py`, `app/services/feed.py`, schemas. **Test:** `tests/test_feed_service.py`, `tests/test_responses_service.py`.
- `feed(db, master_user, cursor) -> FeedPage` (matching per spec D3 using `master_search` arrays; visibility re-check); `respond(db, master_user, request_id, ResponseInput)` (eligible master, not own, open, not paused; lock request row; insert or revive; increment `responses_count`; pause at threshold; enqueue `new_response` job in PR C — leave a single `enqueue_notification` call site stubbed to a no-op function in `app/services/jobs.py` created here with signature `enqueue(db, kind, payload, *, dedupe_key=None, run_at=None) -> None`, real implementation in Task 7); `withdraw(db, master_user, request_id)`; `my_responses(db, master_user) -> list[MyResponseCard]` with statuses (spec D10.9).
- [ ] RED tests: matching matrix (district/whole city/no district; specialty mismatch; own request; already responded; paused; hidden; unpublished/blocked master sees nothing); cursor pagination stable; double respond → one row (Review Focus 2, two sessions); 15th response pauses; withdraw/re-respond counts; foreign ids → not_found → implement → full checks → commit `feat(feed): masters' feed and responses`.

### Task 5: Lifecycle and contacts
**Files:** `app/services/requests.py` (choose/complete), schemas `ClientContactView`. **Test:** `tests/test_lifecycle_service.py`.
- `choose_master(db, client, request_id, response_id, client_phone)` (open; response active; phone normalized; lock; enqueue `chosen`); `complete_request(db, client, request_id)` (master_chosen → completed; enqueue `review_prompt`); close/cancel after choosing enqueues `request_cancelled` to the chosen master; `client_contact_for_master(db, master_user, request_id) -> ClientContactView | None` (only the chosen master: name + client_phone).
- [ ] RED tests: transitions and stale double-clicks (Review Focus 2); phone required/normalized; only responders choosable; blocked master's responses hidden from the client list and not choosable (Review Focus 3); contact view None for non-chosen masters and before choosing → implement → full checks → commit `feat(requests): choose a master and complete the job`.

### Task 6: Feed and response pages
**Files:** `app/web/feed.py`, templates `pages/feed.html`, `pages/requests/detail_master.html`, `my_responses.html`, client detail additions (responses list with master cards + contacts, choose form with phone, complete button). **Test:** `tests/test_feed_web.py`.
- Routes: `GET /feed`, `GET /responses/mine`, `POST /requests/{id}/respond`, `/withdraw`, `/choose`, `/complete`; `GET /requests/{id}` dispatches client/master/404 per spec D10.9.
- [ ] RED tests: feed visible only to eligible masters; master request page never shows address or client contacts before choice, shows phone after; "no longer available" for responders when hidden/deleted, 404 for others (Review Focus 1); someone-else-tries for every POST; no contacts of masters to guests → implement → full checks → commit `feat(feed): feed, responses and lifecycle pages`.

### Task 7: Jobs and worker
**Files:** `app/services/jobs.py` (real `enqueue`, `claim`, `run_once`), `app/worker/__main__.py`, `app/worker/loop.py`, `app/worker/periodic.py` (C4/C5 + purge expired auth rows), `app/cli.py` (`worker run-once`), compose `worker` service (profile `worker`). **Test:** `tests/test_jobs.py`, `tests/test_periodic.py`.
- [ ] RED tests: enqueue in a rolled-back transaction → no job; dedupe; claim with SKIP LOCKED across two sessions (no double claim); retry/backoff/max attempts; stale lock reclaim; handler exception doesn't stop other jobs (Review Focus 4); C4/C5 thresholds with a frozen clock incl. the day edges (Review Focus 5); purge deletes only expired rows → implement → full checks → commit `feat(worker): Postgres job queue, worker loop and periodic rules`.

### Task 8: Notifications
**Files:** `app/services/notifications.py` (prefs, render texts, handlers per event, fan-out), `app/services/telegram_send.py` (stdlib HTTP `sendMessage`, delivery semantics), email via `EmailSender`, `app/bot/dialog.py` (clear `telegram_blocked_at` on any update), `app/web/notifications.py` + account section (events × channels toggles, blocked-bot notice), locales. **Test:** `tests/test_notifications.py`, `tests/test_notifications_web.py`.
- [ ] RED tests: each event's recipients and texts (no address/phone/email of the other side); prefs honoured per channel; blocked users skipped (Review Focus 3); 403 sets telegram_blocked_at and stops; retry rules (connect error → retry; read timeout → no retry); fan-out caps (200 per request, 20 per master per day); prefs page someone-else-tries and CSRF → implement → full checks → commit `feat(notifications): Telegram and email notifications with per-event settings`.

### Task 9: Staging wiring and docs
**Files:** `app/bot/runner.py` (start the worker loop in a daemon thread when `RUN_WORKER=1`), `app/services/jobs.py` (best-effort wake-up ping to `WORKER_WAKE_URL` after commit, 1 s timeout, background thread), settings, `render.yaml` (bot: `RUN_WORKER=1`; site: `WORKER_WAKE_URL=https://master-com-bot-staging.onrender.com/health`), `.env.example`, `docs/INFRASTRUCTURE.md`, `docs/DECISIONS.md` R-026, `README.md`, `docs/PRODUCT_RULES.md` (C3–C5 implemented). **Test:** `tests/test_worker_wiring.py`.
- [ ] RED tests: bot runner starts the thread only with RUN_WORKER=1; a crashing loop restarts and the bot keeps serving (Review Focus 4); wake ping fires after commit only, never blocks the request, never sends anything sensitive; web never imports aiogram (existing test) → implement → full checks → commit `feat(worker): run the worker in the bot service on staging`.
