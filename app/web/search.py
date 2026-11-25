"""The /search page (spec D2, D3, D11.3). Plain GET form; no contacts, no per-user data.

The page keeps every filter in the URL. Links (pills, sort, pages, suggestions) are built here
from the cleaned query, so values the service ignored never travel on.
"""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.db import DbSession
from app.core.i18n import translate
from app.models.user import User
from app.schemas.search import SearchOptions, SearchQuery, SearchResult, Sort
from app.services import search
from app.services.search_options import search_options
from app.web.deps import current_lang, current_user
from app.web.render import render

router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User | None, Depends(current_user)]

# Parameter order in generated links.
ORDER = ("q", "s", "cat", "c", "d", "available", "sort", "page")


class Pill(BaseModel):
    label: str
    remove_href: str


class SortLink(BaseModel):
    value: Sort
    text_key: str
    href: str
    current: bool


def href(params: dict[str, str]) -> str:
    """`/search` with the given parameters in a fixed order (blank values left out)."""
    ordered = [(k, params[k]) for k in ORDER if params.get(k)]
    return "/search" + (f"?{urlencode(ordered)}" if ordered else "")


def clean_params(query: SearchQuery, ignored: set[str]) -> dict[str, str]:
    """The URL parameters that took effect: unknown slugs and a stray district are dropped."""
    params: dict[str, str] = {}
    if query.q:
        params["q"] = query.q
    if query.specialty and "specialty" not in ignored:
        params["s"] = query.specialty
    if query.category and "category" not in ignored:
        params["cat"] = query.category
    if query.city and "city" not in ignored:
        params["c"] = query.city
        if query.district and "district" not in ignored:
            params["d"] = query.district
    if query.available:
        params["available"] = "1"
    if query.sort:
        params["sort"] = query.sort
    return params


def _names(options: SearchOptions) -> dict[str, dict[str, str]]:
    return {
        "s": {s.slug: s.name for c in options.categories for s in c.specialties},
        "cat": {c.slug: c.name for c in options.categories},
        "c": {c.slug: c.name for c in options.cities},
        "d": {d.slug: d.name for d in options.districts},
    }


def pills(params: dict[str, str], options: SearchOptions, available_label: str) -> list[Pill]:
    names = _names(options)
    found = []
    for key in ORDER:
        if key not in params or key in ("sort", "page"):
            continue
        without = {k: v for k, v in params.items() if k != key}
        if key == "c":
            without.pop("d", None)  # a district means nothing without its city
        if key == "q":
            label = params[key]
        elif key == "available":
            label = available_label
        else:
            label = names[key].get(params[key], params[key])
        found.append(Pill(label=label, remove_href=href(without)))
    return found


def suggestion_href(result: SearchResult, params: dict[str, str]) -> str | None:
    """The "did you mean" link: sets the suggested filter and drops the text it came from."""
    hint = result.suggestion
    if hint is None:
        return None
    link = {k: v for k, v in params.items() if k != "q"}
    if hint.kind == "specialty":
        link["s"] = hint.slug
    else:
        link.pop("d", None)
        link["c"] = hint.city_slug if hint.kind == "district" and hint.city_slug else hint.slug
        if hint.kind == "district":
            link["d"] = hint.slug
    return href(link)


def sort_links(params: dict[str, str], effective: Sort, has_text: bool) -> list[SortLink]:
    choices: list[tuple[Sort, str]] = [
        ("rating", "search.sort_rating"),
        ("newest", "search.sort_newest"),
    ]
    if has_text:
        choices.insert(0, ("relevance", "search.sort_relevance"))
    return [
        SortLink(
            value=value,
            text_key=key,
            href=href({**params, "sort": value}),
            current=value == effective,
        )
        for value, key in choices
    ]


@router.get("/search", name="search_page")
def search_page(request: Request, db: DbSession, user: CurrentUser) -> HTMLResponse:
    lang = current_lang(request)
    query, notices = search.parse_query(dict(request.query_params))
    result = search.search(db, query, lang=lang)
    codes = list(dict.fromkeys([*notices, *result.notices]))
    params = clean_params(query, set(codes))
    options = search_options(db, lang=lang, city=params.get("c"))
    has_text = bool(search.query_words(query.q))
    effective: Sort = query.sort or ("relevance" if has_text else "rating")
    if effective == "relevance" and not has_text:
        effective = "rating"
    hint_href = None
    if result.specialty_hint is not None:
        hint_href = href({**params, "s": result.specialty_hint.slug})

    def page_href(number: int) -> str:
        return href({**params, "page": str(number)} if number > 1 else params)

    return render(
        request,
        "pages/search.html",
        user=user,
        form=params,
        options=options,
        result=result,
        notices=[{"code": c, "text_key": f"search.notice_{c}"} for c in codes],
        pills=pills(params, options, translate(lang, "search.available")),
        sorts=sort_links(params, effective, has_text),
        effective_sort=effective,
        hint_href=hint_href,
        suggestion_href=suggestion_href(result, params),
        prev_href=page_href(result.page - 1) if result.page > 1 else None,
        next_href=page_href(result.page + 1) if result.page < result.pages else None,
        has_text=has_text,
    )
