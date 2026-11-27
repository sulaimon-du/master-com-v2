"""Helpers shared by the request pages (`app.web.requests`) and the feed pages (`app.web.feed`)."""

from typing import Annotated
from urllib.parse import quote

from fastapi import Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models.user import User
from app.web.auth import notice_page
from app.web.templating import REQUEST_DONE_CODES

# 32-bit ids: a bigger number can never be a row, and the database would refuse it.
RequestId = Annotated[int, Path(ge=1, le=2_147_483_647)]

# Banners after a redirect; anything else in the query is ignored.
DONE = set(REQUEST_DONE_CODES)


def request_url(request_id: int, query: str = "") -> str:
    return f"/requests/{request_id}{query}"


def signin_redirect(path: str) -> RedirectResponse:
    # The path may carry a query (the prefill); encode it so it stays inside `next`.
    return RedirectResponse(f"/signin?next={quote(path, safe='/')}", status_code=303)


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def not_found_page(request: Request, user: User | None) -> HTMLResponse:
    return notice_page(request, user, status=404, title="error.not_found", text="error.not_found")
