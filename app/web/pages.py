from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.models.user import User
from app.web.deps import current_user
from app.web.render import render

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse, name="home")
def home(request: Request, user: Annotated[User | None, Depends(current_user)]) -> HTMLResponse:
    return render(request, "pages/home.html", user=user, env=get_settings().app_env)
