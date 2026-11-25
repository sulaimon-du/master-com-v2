from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.db import DbSession
from app.models.user import User
from app.services.search_options import search_options
from app.web.deps import current_lang, current_user
from app.web.render import render

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse, name="home")
def home(
    request: Request, db: DbSession, user: Annotated[User | None, Depends(current_user)]
) -> HTMLResponse:
    options = search_options(db, lang=current_lang(request))
    return render(request, "pages/home.html", user=user, options=options)
