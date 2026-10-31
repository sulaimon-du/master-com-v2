# ---- 1. Static assets: Tailwind CSS, vendored htmx + Alpine (CSP build), self-hosted fonts ----
FROM node:22.22.0-bookworm-slim AS assets
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY scripts ./scripts
COPY assets ./assets
COPY app/templates ./app/templates
RUN mkdir -p app/static && npm run build

# ---- 2. Python dependencies ----
FROM python:3.15-slim-bookworm AS deps
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- 3. Runtime ----
FROM python:3.15-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" \
    FORWARDED_ALLOW_IPS="127.0.0.1"
RUN groupadd --system app && useradd --system --gid app --home /app --no-create-home app
WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY --from=assets /src/app/static ./app/static
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health',timeout=2)" || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
