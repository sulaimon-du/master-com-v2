from functools import partial

from fastapi import Request
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.core.i18n import translate
from app.core.security import new_token
from app.models.user import User
from app.schemas.account import AccountView
from app.web.cookies import CSRF, CSRF_MAX_AGE, cookie_name, set_cookie
from app.web.deps import current_lang
from app.web.templating import templates


def render(
    request: Request,
    template: str,
    *,
    user: User | None = None,
    status: int = 200,
    **context: object,
) -> HTMLResponse:
    """Render a page with translations and a CSRF token. Every page goes through here."""
    settings = get_settings()
    lang = current_lang(request)
    existing = request.cookies.get(cookie_name(CSRF, settings))
    token = existing or new_token()
    page = {
        "app_name": settings.app_name,
        "lang": lang,
        "t": partial(translate, lang),
        "csrf_token": token,
        # Templates get a view without sensitive fields, never the ORM object.
        "user": AccountView.from_user(user) if user else None,
        "google_enabled": settings.google_enabled,
        **context,
    }
    response = templates.TemplateResponse(request, template, page, status_code=status)
    # Pages carry the CSRF token and, when signed in, the user's email: never cache them.
    response.headers["Cache-Control"] = "no-store"
    if existing is None:
        set_cookie(response, CSRF, token, settings, max_age=CSRF_MAX_AGE)
    return response
