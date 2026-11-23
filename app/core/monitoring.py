"""Sentry error monitoring (PRODUCT_RULES A3, R-024) for the web app and the Telegram bot.

On only when `SENTRY_DSN` is set. Nothing personal or secret leaves the process: `scrub` runs on
every event and `scrub_breadcrumb` on every breadcrumb before they are queued for sending.
This module must never import aiogram (the web process never loads it) or app.web.
"""

import ipaddress
import logging
import re
from typing import Any, Literal
from urllib.parse import unquote, urlsplit, urlunsplit

import sentry_sdk
from pydantic import SecretStr
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger

from app.core.config import Settings

Component = Literal["web", "bot"]

REDACTED = "[redacted]"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s()-]{6,}")
# Hex and base64url strings of 32+ characters: session IDs, sign-in tokens, API keys.
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,}")
TELEGRAM_SIGNIN_RE = re.compile(r"(/auth/telegram/)[^/]+")
# A query string inside a message (an access-log line, a URL in an error): `?` up to a space/quote.
QUERY_RE = re.compile(r"\?[^\s\"']+")
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# Candidates only; each match is checked with `ipaddress`, so times like 12:30:45 stay.
IPV6_RE = re.compile(r"(?<![\w:.])[0-9A-Fa-f.]*(?::[0-9A-Fa-f.]*){2,}(?![\w:])")
# SQLAlchemy appends bound values as "[parameters: ...]" before "(Background on this error ...)";
# psycopg's DETAIL line quotes the offending row values.
SQL_PARAMS_RE = re.compile(r"\[parameters:.*?(?=\n?\(Background on this error|\Z)", re.DOTALL)
SQL_DETAIL_RE = re.compile(r"DETAIL:[^\n]*")
KEPT_CONTEXTS = frozenset({"runtime", "os", "trace"})
# Access logs carry the visitor's address and the full query string: never recorded at all.
ACCESS_LOGGERS = ("uvicorn.access", "aiohttp.access")
DROPPED_LOGGERS = ("botocore", "urllib3", *ACCESS_LOGGERS)
# Secrets shorter than this are not matched as substrings (they would redact ordinary words).
MIN_SECRET_LEN = 6

_state: dict[str, Any] = {"enabled": False, "secrets": (), "component": None}


def _reset_for_tests() -> None:
    _state.update(enabled=False, secrets=(), component=None)


def _secret_values(settings: Settings) -> tuple[str, ...]:
    """Every SecretStr setting's value, plus the database password; longest first."""
    values: set[str] = set()
    for name in type(settings).model_fields:
        value = getattr(settings, name, None)
        if isinstance(value, SecretStr):
            values.add(value.get_secret_value())
    try:
        password = urlsplit(str(settings.database_url)).password
    except ValueError:
        password = None
    if password:
        values.update({password, unquote(password)})
    return tuple(sorted((v for v in values if len(v) >= MIN_SECRET_LEN), key=len, reverse=True))


def sentry_options(settings: Settings, *, component: Component) -> dict[str, Any]:
    """The `sentry_sdk.init` arguments; also arms `scrub` with this process's secret values."""
    _state["secrets"] = _secret_values(settings)
    _state["component"] = component
    for name in ACCESS_LOGGERS:
        ignore_logger(name)
    dsn = settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else None
    return {
        "dsn": dsn,
        "environment": settings.app_env,
        "release": settings.sentry_release,
        "server_name": component,  # never the host name
        "send_default_pii": False,
        "traces_sample_rate": 0.0,
        "profiles_sample_rate": 0.0,
        "max_breadcrumbs": 30,
        "include_local_variables": False,
        "max_request_body_size": "never",
        # Errors only, no tracing: never add sentry-trace/baggage headers to outgoing requests.
        "trace_propagation_targets": [],
        "before_send": scrub,
        "before_breadcrumb": scrub_breadcrumb,
        # Expected answers, not errors. HTTPException < 500 is also dropped in `scrub`.
        "ignore_errors": ["app.web.csrf.CSRFError"],
        # ERROR logs become events, INFO and above breadcrumbs (FastAPI/Starlette auto-enable).
        "integrations": [LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    }


def init_sentry(settings: Settings, *, component: Component) -> bool:
    """Start Sentry when `SENTRY_DSN` is set; returns whether it is on. Safe to call twice."""
    if _state["enabled"]:
        return True
    if settings.sentry_dsn is None:
        return False
    sentry_sdk.init(**sentry_options(settings, component=component))
    sentry_sdk.set_tag("component", component)
    _state["enabled"] = True
    return True


def _redact_ip(match: re.Match[str]) -> str:
    text = match.group(0)
    candidate = text.rstrip(".")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return text
    return REDACTED + text[len(candidate) :]


def redact(value: str) -> str:
    """Replace secrets, SQL parameters, query strings, IP and email addresses, phone numbers
    and long tokens in a string."""
    for secret in _state["secrets"]:
        value = value.replace(secret, REDACTED)
    value = SQL_PARAMS_RE.sub("[parameters: " + REDACTED + "]", value)
    value = SQL_DETAIL_RE.sub("DETAIL: " + REDACTED, value)
    value = QUERY_RE.sub("", value)
    value = IPV6_RE.sub(_redact_ip, value)
    value = IPV4_RE.sub(_redact_ip, value)
    value = EMAIL_RE.sub(REDACTED, value)
    value = TOKEN_RE.sub(REDACTED, value)
    return PHONE_RE.sub(REDACTED, value)


def _redact_any(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_any(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_any(v) for v in value]
    return value


def _strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _clean_path(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    path = urlsplit(url).path or "/"
    path = TELEGRAM_SIGNIN_RE.sub(lambda m: m.group(1) + REDACTED, path)
    return redact(path)


def _clean_request(request: Any) -> dict[str, Any] | None:
    if not isinstance(request, dict):
        return None
    cleaned: dict[str, Any] = {}
    if isinstance(request.get("method"), str):
        cleaned["method"] = request["method"]
    path = _clean_path(request.get("url"))
    if path is not None:
        cleaned["url"] = path
    return cleaned


def _clean_crumb(crumb: Any) -> dict[str, Any] | None:
    if not isinstance(crumb, dict):
        return None
    category = crumb.get("category")
    if isinstance(category, str) and category.startswith(DROPPED_LOGGERS):
        return None
    crumb = dict(crumb)
    data = crumb.get("data")
    if isinstance(data, dict):
        data = dict(data)
        if isinstance(data.get("url"), str):
            data["url"] = _strip_query(data["url"])
        data.pop("http.query", None)
        data.pop("http.fragment", None)
        crumb["data"] = _redact_any(data)
    elif "data" in crumb:
        crumb.pop("data")
    if isinstance(crumb.get("message"), str):
        crumb["message"] = redact(crumb["message"])
    return crumb


def _is_expected(hint: Any) -> bool:
    exc_info = hint.get("exc_info") if isinstance(hint, dict) else None
    if not exc_info or len(exc_info) < 2:
        return False
    status = getattr(exc_info[1], "status_code", None)
    return isinstance(status, int) and status < 500


def _scrub(event: dict[str, Any], hint: Any) -> dict[str, Any] | None:
    if _is_expected(hint):
        return None
    event.pop("user", None)
    event.pop("extra", None)
    request = _clean_request(event.pop("request", None))
    if request:
        event["request"] = request
    contexts = event.get("contexts")
    if isinstance(contexts, dict):
        event["contexts"] = {k: v for k, v in contexts.items() if k in KEPT_CONTEXTS}
    else:
        event.pop("contexts", None)

    exception = event.get("exception")
    values = exception.get("values") if isinstance(exception, dict) else None
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("value"), str):
                item["value"] = redact(item["value"])
            stack = item.get("stacktrace")
            frames = stack.get("frames") if isinstance(stack, dict) else None
            for frame in frames if isinstance(frames, list) else ():
                if isinstance(frame, dict):
                    frame.pop("vars", None)
    if isinstance(event.get("message"), str):
        event["message"] = redact(event["message"])
    if isinstance(event.get("logentry"), dict):
        event["logentry"] = _redact_any(event["logentry"])
    crumbs = event.get("breadcrumbs")
    crumb_list = crumbs.get("values") if isinstance(crumbs, dict) else None
    if isinstance(crumb_list, list):
        crumbs["values"] = [c for c in map(_clean_crumb, crumb_list) if c is not None]
    if _state["component"]:
        tags = event.get("tags")
        event["tags"] = {
            **(tags if isinstance(tags, dict) else {}),
            "component": _state["component"],
        }
    return event


def _fallback(event: Any) -> dict[str, Any] | None:
    """Strip everything that could carry personal data; drop the event if even that fails."""
    try:
        if not isinstance(event, dict):
            return None
        for key in ("request", "user", "extra", "breadcrumbs", "logentry", "message"):
            event.pop(key, None)
        contexts = event.get("contexts")
        event["contexts"] = (
            {k: v for k, v in contexts.items() if k in KEPT_CONTEXTS}
            if isinstance(contexts, dict)
            else {}
        )
        exception = event.get("exception")
        values = exception.get("values") if isinstance(exception, dict) else None
        for item in values if isinstance(values, list) else ():
            if isinstance(item, dict):
                item["value"] = REDACTED
        return event
    except Exception:
        return None


def scrub(event: Any, hint: Any) -> dict[str, Any] | None:
    """`before_send`: remove personal data and secrets from an event. Never raises."""
    try:
        if not isinstance(event, dict):
            return None
        return _scrub(event, hint)
    except Exception:
        return _fallback(event)


def scrub_breadcrumb(crumb: Any, hint: Any) -> dict[str, Any] | None:
    """`before_breadcrumb`: no query strings, no boto/urllib3 noise, redacted messages."""
    try:
        return _clean_crumb(crumb)
    except Exception:
        return None
