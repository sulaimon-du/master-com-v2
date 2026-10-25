"""Helpers shared by the auth tests."""

import re

from fastapi.testclient import TestClient

from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.core.time import utcnow
from app.models.user import User

TOKEN_RE = re.compile(r"token=([A-Za-z0-9_-]+)")
SESSION = "mc_session"
CSRF = "mc_csrf"
PASSWORD = "correct horse 9"


def make_user(
    email: str = "ana@example.com",
    password: str = PASSWORD,
    *,
    confirmed: bool = True,
    name: str = "Ана",
    google_sub: str | None = None,
    blocked: bool = False,
) -> int:
    """Create an account directly in the database (committed, like the app would)."""
    with get_sessionmaker()() as db:
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            email_verified_at=utcnow() if confirmed else None,
            google_sub=google_sub,
            blocked_at=utcnow() if blocked else None,
            locale="ru",
        )
        db.add(user)
        db.commit()
        return user.id


def csrf_token(client: TestClient) -> str:
    token = client.cookies.get(CSRF)
    if token is None:
        client.get("/signin")
        token = client.cookies.get(CSRF)
    assert token is not None
    return token


def post(client: TestClient, url: str, data: dict[str, str] | None = None, **kwargs):  # type: ignore[no-untyped-def]
    """POST a form with the CSRF token the browser would send."""
    return client.post(url, data={**(data or {}), "csrf_token": csrf_token(client)}, **kwargs)


def sign_in(client: TestClient, email: str = "ana@example.com", password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return post(client, "/signin", {"email": email, "password": password, "next": "/account"})


def link_from(outbox) -> str:  # type: ignore[no-untyped-def]
    """The token in the last email sent, e.g. from a confirmation or reset link."""
    match = TOKEN_RE.search(outbox.sent[-1].body)
    assert match is not None, outbox.sent[-1].body
    return match.group(1)
