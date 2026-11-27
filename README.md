# UstoiMan (working name; repository master-com-v2)

Marketplace for construction and home-repair masters in Tajikistan: photo portfolios, master profiles and an order exchange. Product spec: [`docs/BRIEF.md`](docs/BRIEF.md). Decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md).

Stack: Python 3.12 · FastAPI (sync) · SQLAlchemy 2.0 · Alembic · PostgreSQL 16 · Jinja2 + HTMX + Alpine.js (CSP build) + Tailwind CSS v4.

## Run locally

Everything in Docker:

```bash
cp .env.example .env          # then set SECRET_KEY
docker compose up --build     # http://localhost:8000
```

Or for development with auto-reload (needs Python 3.12, uv, Node 22 and a local Postgres):

```bash
uv sync && npm ci && npm run build
uv run alembic upgrade head
uv run python -m app.seed     # cities, districts, catalog (safe to rerun)
uv run uvicorn --factory app.main:create_app --reload
```

## Sign-in in development

- On http://localhost the cookies are named `mc_session`, `mc_csrf`, `mc_oauth` (no `__Host-` prefix, no `Secure`). Staging and production use `__Host-mc_session` etc. over HTTPS (R-019).
- Emails (confirmation, password reset) are written to the terminal log; open the link from there.
- Leave `CLIENT_IP_HEADER` empty locally; rate limits then count the connection address.
- Google requests for unconfirmed accounts wait for the owner: `uv run python -m app.cli claims list`, then `claims approve <id>` or `claims reject <id>` (R-019).
- Google sign-in is hidden unless `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set in `.env`. For local testing, add `http://localhost:8000/auth/google/callback` as a redirect URI in the Google console.

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
uv run alembic check
uv run pip-audit
```

CI runs all of these on every push and pull request, plus a Docker image build.

## Data

Schema and rules: [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md). Reference data lives in `app/seed/data/`; Tajik names are in `tg_names.csv` for review.

## Endpoints so far

| Path | Purpose |
|---|---|
| `/` | Home page (placeholder) |
| `/health` | Liveness: the process is up |
| `/health/ready` | Readiness: the database answers (503 if not) |
| `/signin`, `/register` | Password sign-in, registration |
| `POST /signout` | Sign out of this browser |
| `/confirm`, `/confirm/resend` | Email confirmation |
| `/password/forgot`, `/password/reset` | Password reset |
| `/auth/google/start`, `/auth/google/callback` | Google sign-in (only when configured) |
| `/auth/telegram/{token}` | Telegram sign-in: GET shows "Sign in as …?", POST signs in (link from the bot, one use, 10 minutes) |
| `/account` | Account settings: sign-in methods, link Google or Telegram, verify phone, sign out everywhere |
| `POST /account/google/link` | Start linking Google to the signed-in account |
| `POST /account/telegram/link` | Start linking Telegram or verifying the phone: redirects to the bot |
| `POST /account/sessions/revoke` | Sign out on every device |
| `/master/onboarding/basics`, `/specialties`, `/area`, `/phone` | Master onboarding wizard steps (signed in) |
| `/master/onboarding/collection` | Onboarding step 5: first collection, optional (signed in) |
| `/master/onboarding/phone/check` | "Check again" for the verified phone |
| `/master/slug-check` | Live check of a profile address (HTMX) |
| `/master/edit` | Edit the master profile |
| `POST /master/avatar` | Upload the profile photo (only when `UPLOADS_ENABLED`) |
| `/master/portfolio`, `/master/portfolio/collections/{id}` (+ POSTs: create, edit, hide, move, upload photos, move/delete/undo photo) | Portfolio management (signed in; uploads only when `UPLOADS_ENABLED`) |
| `/search` | Search masters: `q`, `s`, `cat`, `c`, `d`, `available=1`, `sort`, `page` (no contacts; `noindex`) |
| `/{city}/{specialty}`, `/{city}/{district}/{specialty}`, `/tg/...` | SEO landing pages in ru and tg (no contacts; `index` from 3 masters, else `noindex`) |
| `/robots.txt` | Closed to all outside production; production rules plus the sitemap line |
| `/sitemap.xml` | Indexable SEO pages (ru/tg alternates), visible profiles and public collections; cached 10 minutes |
| `/m/{slug}` | Public master profile (drafts only for the owner) |
| `/m/{slug}/c/{id}` | Public collection gallery (hidden or empty collections and drafts: 404, except for the owner) |
| `/media/{key}` | Uploaded images from local disk |
| `/lang/{code}` | Switch language (`ru`, `tg`) and return to the same page |

The bot process (webhook mode only) serves `POST /telegram/webhook` (Telegram's updates, checked by the `X-Telegram-Bot-Api-Secret-Token` header) and `GET /health` (with `RUN_WORKER=1` it also makes the worker thread poll at once).

## Telegram bot

The bot (`app/bot/`) is a separate process with aiogram, the only async code; the web app never imports it (R-003, R-021). It reaches the database through the same sync services.

**Two bots, one per environment.** Telegram refuses polling while a webhook is set, so create two bots in [@BotFather](https://t.me/BotFather) with `/newbot`: a development bot (any name, e.g. `MasterComDevBot`) and the staging bot `MasterComStagingBot`. Production gets a third one later. Never put the staging token in a local `.env`; the bot refuses polling when `APP_ENV` is `staging` or `production`.

**Locally** (long polling, no public URL needed). In `.env` set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` of the development bot, leave `TELEGRAM_WEBHOOK_URL` empty and keep `SITE_URL=http://localhost:8000`. Then either:

```bash
docker compose --profile bot up --build   # web + db + bot (plain `docker compose up` starts web + db)
uv run python -m app.bot         # or the bot alone, next to `uvicorn` from "Run locally"
```

Open the development bot in Telegram and send `/start`. The site shows the Telegram buttons once `TELEGRAM_BOT_USERNAME` is set; only the bot process reads the token. The bot never runs migrations or the seed; start the web app (or `alembic upgrade head`) first. In Docker the same image starts the bot when a command is given: `deploy/entrypoint.sh` runs its arguments (`python -m app.bot`) instead of migrations, seed and uvicorn.

**Staging** (webhook). `render.yaml` defines a second free web service, `master-com-bot-staging`, from the same image with `dockerCommand: python -m app.bot`. In the Render dashboard:

0. Bot username: bot usernames are global. If BotFather refuses `MasterComStagingBot`, pick another and change both `value:` entries in `render.yaml` (web and bot service) in a commit, not only in the dashboard. After the next Blueprint sync the new service `master-com-bot-staging` appears; open it and fill in the `sync: false` values. Render's free 750 instance-hours per month are shared by the site and the bot service.
1. Bot service: paste `DATABASE_URL` (same Neon string as the site), `TELEGRAM_BOT_TOKEN` (staging bot) and `TELEGRAM_WEBHOOK_SECRET` (random; use Render's **Generate** button). `SECRET_KEY` is generated by Render (`generateValue`); nothing to paste, and it need not match the site's: it only signs the bot's own buttons. If you create the service by hand instead of a Blueprint sync, press **Generate** for it too. `TELEGRAM_WEBHOOK_URL` is `https://master-com-bot-staging.onrender.com/telegram/webhook`; the bot registers it with Telegram on every start.
2. Site service: check `TELEGRAM_BOT_USERNAME=MasterComStagingBot` and `SITE_URL=https://master-com-staging.onrender.com`. The site shows the Telegram buttons once the username is set; it never calls Telegram, so **do not** give it `TELEGRAM_BOT_TOKEN` (the token lives only on the bot service, R-021). A Blueprint change may not reach an existing service, so check Environment by hand.
3. The free service sleeps after 15 minutes; Telegram's next update wakes it and Telegram retries until it answers, so the first reply after a pause can take about a minute.

## Background worker

Notifications and the request timers (C4/C5) run in the worker (`app/worker/`, plain sync code, no aiogram), which takes jobs from the `jobs` table in Postgres (spec 2026-11-26 D6, D7). Locally it is its own process:

```bash
uv run python -m app.worker                 # next to uvicorn
docker compose --profile worker up --build  # or web + db + worker
uv run python -m app.cli worker run-once    # run every due job once and exit (debugging)
```

Without a running worker, jobs wait in the table and run once a worker starts. On staging the worker runs as a thread inside the bot service (`RUN_WORKER=1`), and the site pings the bot's `/health` (`WORKER_WAKE_URL`) after queuing a job so it runs at once; see [`docs/INFRASTRUCTURE.md`](docs/INFRASTRUCTURE.md), "Background jobs". Production runs it as its own container.

## Staging

Render free web service (Docker) + Neon free Postgres; details in [`docs/INFRASTRUCTURE.md`](docs/INFRASTRUCTURE.md) and [`render.yaml`](render.yaml). Pushing to `main` deploys automatically; migrations and the seed run on start.
