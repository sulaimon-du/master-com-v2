from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.db import DbSession
from app.core.i18n import translate
from app.models.user import User
from app.schemas.search import HomeMasters
from app.services.home import home_masters
from app.services.search_options import search_options
from app.web.deps import current_lang, current_user
from app.web.render import render
from app.web.search import href as search_href

router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User | None, Depends(current_user)]


class Chip(BaseModel):
    """A quick filter of the home masters block: a plain link, enhanced with HTMX."""

    key: str  # "all", a city slug, or "available"
    label: str
    href: str  # the full home page with the filter (works without JS)
    hx_get: str  # the fragment with the same filter
    current: bool


class MastersBlock(BaseModel):
    chips: list[Chip]
    items: HomeMasters
    search_href: str


def _links(city: str | None, available: bool) -> tuple[str, str]:
    home = [("m_city", city)] if city else []
    frag = [("city", city)] if city else []
    if available:
        home.append(("m_available", "1"))
        frag.append(("available", "1"))
    home_href = "/" + (f"?{urlencode(home)}" if home else "") + "#masters"
    frag_href = "/partials/home-masters" + (f"?{urlencode(frag)}" if frag else "")
    return home_href, frag_href


def _block(view: HomeMasters, lang: str) -> MastersBlock:
    def chip(key: str, label: str, city: str | None, available: bool, current: bool) -> Chip:
        home_href, frag_href = _links(city, available)
        return Chip(key=key, label=label, href=home_href, hx_get=frag_href, current=current)

    chips = [chip("all", translate(lang, "home.masters_all"), None, view.available, not view.city)]
    chips += [
        chip(c.slug, c.name, c.slug, view.available, view.city == c.slug) for c in view.cities
    ]
    chips.append(
        chip(
            "available",
            translate(lang, "home.masters_available"),
            view.city,
            not view.available,
            view.available,
        )
    )
    params = {"c": view.city or "", "available": "1" if view.available else ""}
    return MastersBlock(chips=chips, items=view, search_href=search_href(params))


@router.get("/", response_class=HTMLResponse, name="home")
def home(request: Request, db: DbSession, user: CurrentUser) -> HTMLResponse:
    lang = current_lang(request)
    options = search_options(db, lang=lang)
    view = home_masters(
        db,
        lang=lang,
        city=request.query_params.get("m_city"),
        available=request.query_params.get("m_available"),
    )
    return render(
        request, "pages/home.html", user=user, options=options, masters=_block(view, lang)
    )


@router.get("/partials/home-masters", response_class=HTMLResponse, name="home_masters_partial")
def home_masters_partial(request: Request, db: DbSession, user: CurrentUser) -> HTMLResponse:
    """The masters block alone, for the chips' HTMX swap (same filters as the home page)."""
    lang = current_lang(request)
    view = home_masters(
        db,
        lang=lang,
        city=request.query_params.get("city"),
        available=request.query_params.get("available"),
    )
    return render(request, "partials/home_masters.html", user=user, masters=_block(view, lang))
