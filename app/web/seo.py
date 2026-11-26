"""SEO landing pages (spec D4): `/<city>/<specialty>`, `/<city>/<district>/<specialty>` and the
same under `/tg/`. Registered after every other router, so existing paths always win; any slug
that is not an active city, district of that city or specialty answers 404.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.core.db import DbSession
from app.models.user import User
from app.services import search, seo
from app.web.auth import LANG_MAX_AGE, notice_page
from app.web.cookies import LANG, cookie_name, set_cookie
from app.web.deps import current_user, use_path_lang
from app.web.render import render

router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User | None, Depends(current_user)]


def _landing(
    request: Request,
    db: DbSession,
    user: User | None,
    *,
    lang: str,
    city: str,
    district: str | None,
    specialty: str,
) -> HTMLResponse:
    use_path_lang(request, lang)  # the 404 page too speaks the URL's language
    query, _ = search.parse_query({"page": request.query_params.get("page", "")})
    view = seo.landing(
        db, lang=lang, city=city, district=district, specialty=specialty, page=query.page
    )
    if view is None:
        # Both switch links go home: the other-language URL of a missing page is a 404 too.
        return notice_page(
            request,
            user,
            status=404,
            title="error.not_found",
            text="error.not_found",
            lang_links={"ru": "/", "tg": "/"},
        )
    response = render(
        request,
        "pages/seo_landing.html",
        user=user,
        view=view,
        meta=view.meta,
        lang_links=view.lang_links,
    )
    # The visitor chose this language by following its URL: keep it on the next page (R-025).
    settings = get_settings()
    if request.cookies.get(cookie_name(LANG, settings)) != lang:
        set_cookie(response, LANG, lang, settings, max_age=LANG_MAX_AGE)
    return response


# Tajik first: `/tg/<city>/<specialty>` has the shape of a Russian district page.
@router.get("/tg/{city}/{specialty}", name="seo_city_tg")
def city_page_tg(
    request: Request, db: DbSession, user: CurrentUser, city: str, specialty: str
) -> HTMLResponse:
    return _landing(request, db, user, lang="tg", city=city, district=None, specialty=specialty)


@router.get("/tg/{city}/{district}/{specialty}", name="seo_district_tg")
def district_page_tg(
    request: Request, db: DbSession, user: CurrentUser, city: str, district: str, specialty: str
) -> HTMLResponse:
    return _landing(request, db, user, lang="tg", city=city, district=district, specialty=specialty)


@router.get("/{city}/{specialty}", name="seo_city")
def city_page(
    request: Request, db: DbSession, user: CurrentUser, city: str, specialty: str
) -> HTMLResponse:
    return _landing(request, db, user, lang="ru", city=city, district=None, specialty=specialty)


@router.get("/{city}/{district}/{specialty}", name="seo_district")
def district_page(
    request: Request, db: DbSession, user: CurrentUser, city: str, district: str, specialty: str
) -> HTMLResponse:
    return _landing(request, db, user, lang="ru", city=city, district=district, specialty=specialty)
