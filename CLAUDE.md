# Master COM: rules for Claude Code

A marketplace for construction and home-repair masters in Tajikistan: Instagram-style portfolios, LinkedIn-style profiles and an order exchange. The owner does not review code line by line, so **tests, CI and the reviewer agent are the safety net. Treat them as mandatory.**

## Sources of truth, in order
1. What the owner says in the current task.
2. `docs/DECISIONS.md` (R-001 and later). A later decision overrides an earlier one and the brief.
3. `docs/BRIEF.md`: the product and technical specification for v1.
4. `docs/design_tokens.md`: colors, fonts, radii. Current pick: variant 11 (R-014).

If they disagree or are silent, stop and ask; propose a decision. Never invent requirements. Log every deviation from the brief as a new R-xxx entry in `docs/DECISIONS.md`.

## Stack (do not change without a decision)
- Python 3.12, FastAPI with **sync** routes only, SQLAlchemy 2.0 (sync `Session`), Alembic, PostgreSQL 16, psycopg 3.
- Server-rendered Jinja2 + HTMX + Alpine.js **CSP build** + Tailwind CSS v4 (CLI). No SPA, no public JSON API in v1.
- Admin: SQLAdmin. Images: Pillow → Cloudflare R2. Telegram bot: aiogram in its own process (the only async code; it never shares a process with the web app).
- Background jobs and rate limits in Postgres (`FOR UPDATE SKIP LOCKED`). No Redis in v1.
- Packages: `uv` (Python), `npm` (build tools only). New dependencies need a reason in the PR description.

## Layout
```
app/
  main.py            app factory (create_app), middleware, routers
  core/              config (pydantic-settings), db, security
  api/               non-page endpoints (health, HTMX fragments)
  web/               page routes and templating
  models/            SQLAlchemy models (import every model in models/__init__.py)
  services/          business logic and permission checks (routers stay thin)
  templates/         Jinja2; pages/ and partials/
  static/            build output (gitignored except .gitkeep)
assets/css/app.css   Tailwind entry and design tokens
migrations/          Alembic
tests/               pytest
docs/                brief, decisions, tokens
```

## Commands
- Install: `uv sync && npm ci`
- Build assets: `npm run build` (watch: `npm run watch:css`)
- Run: `uv run uvicorn --factory app.main:create_app --reload`
- Whole stack: `docker compose up --build`
- Checks before every commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest`
- Migrations: `uv run alembic revision --autogenerate -m "..."`, review the file by hand, then `uv run alembic upgrade head && uv run alembic check`

## Security rules (never cut, even for speed)
- The server is the security boundary. Every write checks ownership in a service function; hiding a button is not access control.
- Sessions, not JWT (R-011): 256-bit random ID stored hashed; `__Host-` cookie, HttpOnly, Secure, SameSite=Lax; rotate on sign-in and privilege change; CSRF token on every form and HTMX request.
- Passwords: Argon2id. Login and phone-code endpoints are rate limited.
- Contacts (phone, Telegram, WhatsApp) are rendered only for signed-in users. Guests get "Sign in to see contacts".
- Templates autoescape. Never `|safe` on user data, never `x-html`, no inline `<script>` or `style=""` (CSP is `'self'` only).
- Uploads: check magic bytes, max 10 MB, strip all metadata, re-encode to WebP; never trust the filename or content type.
- No secrets in code, logs or error messages. Settings come from env only.
- Personal data of imported (scraped) profiles follows R-015 and R-016.

## Code rules
- Routers parse input, call a service, render a template or fragment. No business logic in routers or templates.
- Separate Pydantic models for input and output; never pass ORM objects with sensitive fields to templates.
- Constraint violations return 409 or 422 with a friendly message, never 500.
- All user-facing text goes through translations (ru default, tg). Do not hard-code Russian in new templates.
- Mobile first: check every page at 375 px width, light and dark.
- Conventional Commits: `feat(auth): ...`, `fix(search): ...`, `chore(ci): ...`.

## Tests (required in the same PR as the code)
- Auth and sessions, access control (user A can never read or change user B's data), contacts hidden from guests, request lifecycle, chat permissions, uploads, search.
- Every new write endpoint gets at least one "someone else tries" test.
- Tests run against real Postgres (`DATABASE_URL`), never SQLite.

## Before opening a PR
1. All checks pass locally.
2. Run the `reviewer` agent (`.claude/agents/reviewer.md`) on the diff and fix its blocking findings.
3. Fill in the PR template, including how the owner can check the change on staging.
