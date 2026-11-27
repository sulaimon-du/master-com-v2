# Master COM: infrastructure

Status: 2026-11-23 (week 4 merged). Staging only; production comes in week 12 (R-007). Decision: R-017.
**No secrets in this file or anywhere in git.** Secret values live only in the Render dashboard (staging) and in your local `.env`.

## Overview

| Part | Staging | Production (plan, R-007) |
|---|---|---|
| App | Render, free web service, Docker, Frankfurt | One EU VPS (~€6.5/month) with Docker Compose + Caddy |
| Telegram bot | Render, second free web service from the same image, webhook mode (R-021) | a `bot` container on the same VPS |
| Database | Neon, free Postgres, AWS Frankfurt (`aws-eu-central-1`) | Postgres on the same VPS, nightly backups to R2 |
| Images | Cloudflare R2, bucket `master-com-staging-media`, public `r2.dev` URL | Cloudflare R2 |
| Code | GitHub `sulaimon-du/master-com-v2`, branch `main` | same |
| CI | GitHub Actions: lint, tests on Postgres, migration round trip, seed twice, audit, Docker build | same |

Staging URL: https://master-com-staging.onrender.com

## How a change reaches staging

1. A PR is merged into `main`.
2. Render sees the push (`autoDeploy: true` in `render.yaml`), builds the `Dockerfile` and starts the container.
3. `deploy/entrypoint.sh` runs `alembic upgrade head` (because `RUN_MIGRATIONS=1`), then `python -m app.seed` (because `RUN_SEED=1`; the seed is idempotent), then `python -m app.cli search rebuild` (same `RUN_SEED` guard: the search index is rebuilt on every start of the site service, so existing masters are indexed after a deploy; the bot service never runs it), then starts uvicorn.
4. Render checks `GET /health`. When it answers, traffic switches to the new container.

Check after a deploy:
- https://master-com-staging.onrender.com/health/ready returns `{"status":"ok","database":"ok"}`.
- Render → service → **Logs** shows `seed done: SeedStats(...)` and no tracebacks.
- The search index is rebuilt on every start of the site service (`python -m app.cli search rebuild`, run by `entrypoint.sh` right after the seed); to rebuild by hand, run the same command in the service shell.

## Search engines (robots and sitemap)
- Staging (any `APP_ENV` other than `production`) is **never indexed**: every response carries `X-Robots-Tag: noindex, nofollow` and `https://master-com-staging.onrender.com/robots.txt` disallows everything.
- Production `robots.txt` allows crawling except private and search paths and points to `<SITE_URL>/sitemap.xml`. `SITE_URL` must be the public address, because every sitemap and canonical URL is built from it.
- `/sitemap.xml` is built from the database and cached in each web process for 10 minutes; a new or blocked master shows up (or disappears) within that time.

## Render (app)

- Services: `master-com-staging` (site) and `master-com-bot-staging` (Telegram bot, `dockerCommand: python -m app.bot`, no migrations or seed), both from `render.yaml`. Plan: free, region Frankfurt, branch `main`.
- Signed in with the owner's GitHub account; Render has access to `master-com-v2` only.
- **Free-plan limits** (checked 2026-10-24, [render.com/docs/free](https://render.com/docs/free)):
  - sleeps after 15 minutes without traffic; the first request then takes about a minute;
  - 750 instance hours per month per workspace, shared by the site and the bot (both sleep when idle, so this is enough for staging);
  - Hobby workspace: 5 GB outbound traffic per month since the August 2026 plan change; over the limit, free services are suspended if no card is on file;
  - build minutes are limited; failed builds count too.
- Not used: Render's free Postgres. It is deleted 30 days after creation.

### Environment variables on Render

| Name | Value | Where set | Secret |
|---|---|---|---|
| `APP_ENV` | `staging` | `render.yaml` | no |
| `RUN_MIGRATIONS` | `1` | `render.yaml` | no |
| `RUN_SEED` | `1` | `render.yaml` | no |
| `FORWARDED_ALLOW_IPS` | `*` (only Render's proxy reaches the container) | `render.yaml` | no |
| `CLIENT_IP_HEADER` | `CF-Connecting-IP` (address for the IP rate limits; R-019, checked 2026-11-22) | `render.yaml` | no |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in; redirect URI `https://master-com-staging.onrender.com/auth/google/callback` | Render dashboard | **yes** (secret) |
| `TELEGRAM_BOT_USERNAME` | `MasterComStagingBot` (shows the Telegram buttons) | `render.yaml` | no |
| `SITE_URL` | `https://master-com-staging.onrender.com` | `render.yaml` | no |
| `UPLOADS_ENABLED` | `false` until the owner checks R2 after the deploy, then `true`; see "Photos on staging" | `render.yaml`, then Render dashboard | no |
| `R2_ENDPOINT` | R2 S3 API endpoint, `https://<account-id>.r2.cloudflarestorage.com` (Cloudflare → R2 → bucket → Settings) | Render dashboard | **yes** (keep private) |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | R2 API token with **Object Read & Write** on `master-com-staging-media` only | Render dashboard | **yes** (secret) |
| `R2_BUCKET` | `master-com-staging-media` | `render.yaml` | no |
| `R2_PUBLIC_URL` | `https://pub-d77c5213c3b64bc789186e39de9fbe9e.r2.dev` (the bucket's public URL, no trailing slash; also added to the CSP `img-src`) | `render.yaml` | no |
| `MEDIA_ROOT` | folder for uploaded images when R2 is not configured; default `media`. Render's free disk is wiped on every deploy | default (not set) | no |
| `SECRET_KEY` | generated by Render | `render.yaml` (`generateValue`) | **yes** |
| `DATABASE_URL` | Neon direct connection string | Render dashboard | **yes** |
| `RENDER_EXTERNAL_HOSTNAME` | set by Render automatically; the app trusts it as an allowed host | Render | no |
| `SENTRY_DSN` | Sentry DSN for error monitoring; empty = off (see "Sentry") | Render dashboard | **yes** (keep private) |
| `RENDER_GIT_COMMIT` | set by Render automatically; used as the Sentry release | Render | no |
| `WORKER_WAKE_URL` | `https://master-com-bot-staging.onrender.com/health`: after a commit that queued a job the site GETs it so the worker polls at once (see "Background jobs") | `render.yaml` | no |

The site never gets `TELEGRAM_BOT_TOKEN`: it only builds `t.me` links (R-021, least privilege). The bot never gets the `R2_*` variables: it stores no images.

### Photos on staging

Photos are stored in Cloudflare R2 when all five `R2_*` variables are set on the site service; otherwise the site falls back to local disk, which Render wipes on every deploy. With R2 on, images are served from `R2_PUBLIC_URL` and `/media/...` answers 404. A wrong `R2_ENDPOINT` or `R2_PUBLIC_URL` (not `https://`, not a plain host name, a bad port) stops the site at startup with a message naming the variable, never its value. With `UPLOADS_ENABLED=true` on staging or production, the site also refuses to start unless all five `R2_*` variables are set (the message lists the missing names). If only some are set while uploads are off, the site starts on local disk and logs one warning naming the missing ones.

Turning uploads on after the deploy:
1. Render → `master-com-staging` → Environment: check that `R2_ENDPOINT`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` are filled in (they are `sync: false`, so Render does not create them from `render.yaml` on an existing service) and that `R2_BUCKET` and `R2_PUBLIC_URL` are there.
2. Set `UPLOADS_ENABLED` to `true` and save (the service restarts).
3. Upload an avatar: the image address on the profile must start with `R2_PUBLIC_URL`. Redeploy and check that the photo is still there.
If an upload fails, the user sees "could not save, try again"; check the R2 token's permissions and the endpoint.

### Telegram bot service (`master-com-bot-staging`)

| Name | Value | Where set | Secret |
|---|---|---|---|
| `APP_ENV` | `staging` (the bot refuses polling here) | `render.yaml` | no |
| `DATABASE_URL` | the same Neon string as the site | Render dashboard | **yes** |
| `SECRET_KEY` | generated by Render; signs only the bot's own buttons, need not match the site's | `render.yaml` (`generateValue`) | **yes** |
| `TELEGRAM_BOT_TOKEN` | token of the **staging** bot from BotFather (never the development bot) | Render dashboard | **yes** |
| `TELEGRAM_WEBHOOK_SECRET` | random; Telegram sends it with every update, the bot checks it | Render dashboard | **yes** |
| `TELEGRAM_BOT_USERNAME` | `MasterComStagingBot` | `render.yaml` | no |
| `TELEGRAM_WEBHOOK_URL` | `https://master-com-bot-staging.onrender.com/telegram/webhook` (the bot registers it on every start) | `render.yaml` | no |
| `SITE_URL` | `https://master-com-staging.onrender.com` | `render.yaml` | no |
| `RUN_WORKER` | `1`: the job worker runs in a thread of this service (see "Background jobs") | `render.yaml` | no |
| `SENTRY_DSN` | Sentry DSN, the same project as the site; events are tagged `component=bot` | Render dashboard | **yes** (keep private) |

Render does not create new `sync: false` variables from `render.yaml` on an existing service, and a Blueprint change may not reach an existing service at all: after a PR that adds variables, check Environment by hand.

### Background jobs (worker)

Notifications and the request timers (C4 reminder and auto-close, C5 "is the job done?" and close; spec 2026-11-26 D6, D7) run in the worker: `python -m app.worker`, plain sync code that claims jobs from the `jobs` table with `FOR UPDATE SKIP LOCKED`.

- **Staging:** Render's free plan has no background workers, so the worker runs as a **daemon thread inside the bot service** (`RUN_WORKER=1` on `master-com-bot-staging` only). That service already holds `TELEGRAM_BOT_TOKEN` (R-021: the site never gets it) and already reaches the database. A loop that crashes is logged (type and stack frames only; Sentry gets it as an `ERROR` from the bot service) and restarted after 30 seconds; the bot keeps answering whatever the worker does. On shutdown the thread stops after the job in progress (at most 10 seconds; a job cut off is retried after 10 minutes).
- **Wake-up ping:** after a commit that queued a job, the site sends `GET WORKER_WAKE_URL` (the bot's `/health`) from a background thread: no body, no cookies, no ids, 1 second timeout, failures ignored, at most one ping in flight. Every `GET /health` on the bot makes the worker poll at once (never more than once a second); otherwise it polls every 5 seconds.
- **While the bot service sleeps** (15 minutes without traffic), jobs and timers wait. The site's ping wakes it (about a minute), and so does any Telegram update. Timers (C4/C5) run only while it is awake; on staging that is acceptable: the rules count days from stored timestamps, so a late run closes nothing early (a request already past 30 days is closed without the late reminder). While the bot is awake the worker queries the database every 5 seconds, so Neon's compute does not suspend then; it counts against Neon's 100 compute hours only while the bot service is awake.
- **Production (week 12 VPS) and locally:** the worker is its own process (compose service `worker`, `docker compose --profile worker up`, or `uv run python -m app.worker`); leave `RUN_WORKER` and `WORKER_WAKE_URL` empty there.
- **Check it works:** create a request on staging, then in the Neon SQL Editor `SELECT kind, attempts, done_at, failed_at, last_error FROM jobs ORDER BY id DESC LIMIT 10;` shows the `new_request` job with `done_at` set within seconds (or about a minute when the bot was asleep). The bot service log shows `worker started` after each deploy.

Coming next:

| Name | Needed from | Secret |
|---|---|---|
| key for the blocklist hash (R-018; name defined in the PR that implements it) | import tool, weeks 10–11 | yes |
| email provider key | before launch (PRODUCT_RULES A4) | yes |

Changing a variable restarts the service. Sessions do not depend on `SECRET_KEY`: they are random IDs stored hashed in Postgres (R-011, R-019), so changing it signs nobody out. Keep it stable anyway, because later features may key hashes with it (for example the re-import blocklist, R-018). To sign everyone out, delete the rows in `auth_sessions`.

## Sentry (error monitoring)

- Organisation `ustoiman`, **EU region** (data stays in Sentry's EU data centre), free plan (PRODUCT_RULES A3, R-024). Set up by the owner 2026-11-23.
- On only when `SENTRY_DSN` is set; both services use it, told apart by the `component` tag (`web` or `bot`) and `server_name`. `environment` is `APP_ENV`, `release` is `RENDER_GIT_COMMIT` (or `SENTRY_RELEASE`).
- **What is sent:** unhandled exceptions (type, stack trace with source lines, a redacted message), `ERROR` log lines as events, the last 30 `INFO`+ log lines as breadcrumbs (redacted), SQL statement breadcrumbs (the statement text only, never the bound values), outgoing HTTP request breadcrumbs (URL without the query string, token-like path parts redacted), the request method and URL path, runtime and OS versions.
- **What is never sent** (removed in `app/core/monitoring.py` before anything leaves the process): request bodies, cookies, headers and query strings; user IDs, emails and IP addresses; access-log lines (`uvicorn.access`, `aiohttp.access` are not recorded at all); local variables; `extra` and other contexts; SQL bound values (the engine runs with `hide_parameters=True`, and `[parameters: ...]` and psycopg `DETAIL:` lines are cut from messages); outgoing `sentry-trace`/`baggage` headers (`trace_propagation_targets=[]`); in messages: query strings, IPv4/IPv6 addresses, email addresses, phone numbers, 32+ character tokens and the value of every secret setting (`SECRET_KEY`, bot token, webhook secret, R2 keys, Google secret, the DSN, the database password) inside messages; the sign-in token in `/auth/telegram/...`; botocore and urllib3 breadcrumbs. No performance tracing or profiling.
- **Not reported:** expected answers (404 and other 4xx, CSRF failures, rate limits).
- **Rotating the DSN:** Sentry → Settings → Projects → the project → Client Keys (DSN) → Generate New Key, paste the new DSN into `SENTRY_DSN` on both services, then disable and delete the old key. A leaked DSN only lets someone send events into the project; it gives no read access.

## Neon (database)

- Project: `master-com-staging`, region AWS Europe Central 1 (Frankfurt), the same city as the Render service.
- Only the Postgres service is enabled. Neon object storage, functions, AI gateway, realtime and Neon Auth are **not used** (images go to R2, sign-in is our own, R-011).
- Database `neondb`, default role. The app uses the **direct** (not pooled) connection string with `sslmode=require`; the app converts `postgresql://` to the psycopg 3 driver itself.
- **Free-plan limits** (checked 2026-10-22, [neon.com/pricing](https://neon.com/pricing)):
  - 1 GB storage per project; writes are blocked above it;
  - 100 compute hours per project per month; compute suspends after 5 minutes idle (first query then takes a moment);
  - 5 GB outbound transfer per month;
  - restore window 6 hours, one manual snapshot, no scheduled backups.
- Staging data is disposable. Do not put real users' data on staging.

### Useful checks in the Neon SQL Editor

```sql
SELECT count(*) FROM categories;            -- 12 directions
SELECT version_num FROM alembic_version;    -- current migration
SELECT pg_size_pretty(pg_database_size(current_database()));  -- stay well under 1 GB
```

## Local development

- `cp .env.example .env`, set `SECRET_KEY` (48+ random characters), then `docker compose up --build`.
- Postgres from compose listens on `127.0.0.1:5432` only. Never point local `.env` at the Neon database.
- Telegram bot locally: the **development** bot's token and username in `.env`, `TELEGRAM_WEBHOOK_URL` empty, then `docker compose --profile bot up --build` (see README, "Telegram bot"). Never put the staging bot's token in `.env`.

## Secrets policy

- Secrets never go into git, chats, issues, PR descriptions or logs.
- Staging secrets: Render dashboard only. Local secrets: `.env` (gitignored).
- If a secret leaks: rotate it at the source (Neon: reset the role password; Render: regenerate `SECRET_KEY`; to end all sessions, `DELETE FROM auth_sessions;` in the Neon SQL Editor; Google: regenerate in the Google console; Telegram bot: BotFather → `/mybots` → the bot → API Token → Revoke, paste the new token into the bot service only; webhook secret: generate a new one in Render; R2: Cloudflare → R2 → Manage API tokens → roll or delete the token, paste the new pair into the site service only; Sentry DSN: see "Sentry"), update Render, redeploy.

## When something breaks

| Symptom | Likely cause | Fix |
|---|---|---|
| First page load takes ~1 minute | the free service was asleep | normal on staging |
| Deploy fails at `alembic upgrade head` | migration error or Neon unreachable | read Render logs; fix in a PR; Neon status page |
| `/health/ready` returns 503 | database down, wrong `DATABASE_URL`, or Neon over its limits | check Neon dashboard (compute hours, storage) and the variable |
| Free services suspended | 5 GB traffic or build minutes used up | wait for the next month or add a card; review what is serving traffic |
| `400 Invalid host header` | a new domain is not in allowed hosts | add it to `ALLOWED_HOSTS` |
| No Telegram button on `/signin` | `TELEGRAM_BOT_USERNAME` missing on the site service | add it in Render → Environment |
| The bot does not answer | bot service asleep (first reply takes ~1 minute), wrong token or webhook secret, or `TELEGRAM_WEBHOOK_URL` does not match the service address | read the bot service Logs; check the variables |
| Bot logs `refusing polling` | `TELEGRAM_WEBHOOK_URL` unset on staging | set it (see the table above) |
| No notifications; jobs stay without `done_at` | `RUN_WORKER` missing on the bot service, bot service asleep, or `TELEGRAM_BOT_TOKEN` wrong (jobs then show `last_error`) | check the bot service Environment and Logs (`worker started`, `worker loop crashed`) |
| Notifications arrive only after minutes | `WORKER_WAKE_URL` missing on the site service, or the bot service was asleep | add it (see the site table) |
