"""Public master profile page (`/m/<slug>`).

Published profiles are public; a draft is visible only to its owner. Contacts are rendered only
for signed-in users (the service leaves them out of the view for guests).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.db import DbSession
from app.models.user import User
from app.services import masters, portfolio
from app.services.portfolio import PortfolioError
from app.web.auth import notice_page
from app.web.deps import current_lang, current_user
from app.web.render import render

router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User | None, Depends(current_user)]

NOTICES = {"published", "incomplete"}
# The wizard step that fixes each missing item; hidden/inactive need a moderator or support.
FIX_LINKS = {
    "phone": "/master/onboarding/phone",
    "specialty": "/master/onboarding/specialties",
    "area": "/master/onboarding/area",
}


class MissingItem(BaseModel):
    text_key: str
    href: str | None


def _missing_items(codes: list[str]) -> list[MissingItem]:
    return [MissingItem(text_key=f"master.missing_{c}", href=FIX_LINKS.get(c)) for c in codes]


@router.get("/m/{slug}", name="master_profile")
def master_profile(slug: str, request: Request, db: DbSession, user: CurrentUser) -> HTMLResponse:
    master = masters.public_master(db, slug, user)
    if master is None:
        return notice_page(
            request, user, status=404, title="error.not_found", text="error.not_found"
        )
    view = masters.profile_view(db, master, lang=current_lang(request), viewer=user)
    is_owner = user is not None and master.user_id == user.id
    missing = _missing_items(list(masters.missing_for_publish(db, master))) if is_owner else []
    notice = request.query_params.get("notice")
    return render(
        request,
        "pages/master/profile.html",
        user=user,
        profile=view,
        is_owner=is_owner,
        missing=missing if view.is_draft or notice == "incomplete" else [],
        notice=notice if is_owner and notice in NOTICES else None,
        signin_next=f"/signin?next=/m/{view.slug}",
    )


@router.get("/m/{slug}/c/{collection_id}", name="public_collection")
def public_collection(
    slug: str, collection_id: int, request: Request, db: DbSession, user: CurrentUser
) -> HTMLResponse:
    master = masters.public_master(db, slug, user)
    is_owner = master is not None and user is not None and master.user_id == user.id
    try:
        if master is None:
            raise PortfolioError("not_found")
        collection, photos = portfolio.collection_page(db, master, collection_id, owner=is_owner)
    except PortfolioError:
        return notice_page(
            request, user, status=404, title="error.not_found", text="error.not_found"
        )
    view = masters.profile_view(db, master, lang=current_lang(request), viewer=user)
    return render(
        request,
        "pages/master/collection.html",
        user=user,
        profile=view,
        collection=collection,
        photos=photos,
        is_owner=is_owner,
    )
