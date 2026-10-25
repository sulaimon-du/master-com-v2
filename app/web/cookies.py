"""Cookie names and flags in one place.

Staging and production use the `__Host-` prefix with Secure (R-011). Development and tests run
on plain http://localhost, so they use the same names without the prefix or Secure flag.
"""

from fastapi import Response

from app.core.config import Settings

SESSION = "mc_session"
CSRF = "mc_csrf"
OAUTH = "mc_oauth"
LANG = "mc_lang"

CSRF_MAX_AGE = 60 * 60 * 24 * 90
OAUTH_MAX_AGE = 60 * 10


def cookie_name(base: str, settings: Settings) -> str:
    return f"__Host-{base}" if settings.secure_cookies else base


def set_cookie(
    response: Response, base: str, value: str, settings: Settings, *, max_age: int
) -> None:
    response.set_cookie(
        cookie_name(base, settings),
        value,
        max_age=max_age,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def delete_cookie(response: Response, base: str, settings: Settings) -> None:
    response.delete_cookie(
        cookie_name(base, settings),
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
