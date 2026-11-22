"""CSRF protection: double-submit cookie (BRIEF §4.1).

The page renders the token from the `mc_csrf` cookie into every form and HTMX request. A POST is
accepted only when the form field or the `X-CSRF-Token` header equals the cookie. Cross-site
pages cannot read the cookie, so they cannot send a matching value.
"""

from typing import Annotated

from fastapi import Depends, Form, Header, Request

from app.core.config import get_settings
from app.core.security import tokens_match
from app.web.cookies import CSRF, cookie_name


class CSRFError(Exception):
    pass


def verify_csrf(
    request: Request,
    csrf_token: Annotated[str | None, Form()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = request.cookies.get(cookie_name(CSRF, get_settings()))
    given = x_csrf_token or csrf_token
    if not expected or not given or not tokens_match(expected, given):
        raise CSRFError


CSRFProtected = Depends(verify_csrf)
