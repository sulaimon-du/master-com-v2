# Master COM

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
uv run uvicorn --factory app.main:create_app --reload
```

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
uv run alembic check
uv run pip-audit
```

CI runs all of these on every push and pull request, plus a Docker image build.

## Endpoints so far

| Path | Purpose |
|---|---|
| `/` | Home page (placeholder) |
| `/health` | Liveness: the process is up |
| `/health/ready` | Readiness: the database answers (503 if not) |

## Staging

Render free web service (Docker) + Neon free Postgres; see [`render.yaml`](render.yaml). Pushing to `main` deploys automatically; migrations run on start.
