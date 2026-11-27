"""Notification settings: events x channels switches of the signed-in user (spec D8, D10.6).

Only the signed-in user's own switches: there is no URL or form field that names a user.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.core.db import DbSession
from app.models.jobs import CHANNELS, EVENTS
from app.models.user import User
from app.services import notifications
from app.web.csrf import CSRFProtected
from app.web.deps import current_user
from app.web.render import render

router = APIRouter(include_in_schema=False)

CurrentUser = Annotated[User | None, Depends(current_user)]
PAGE = "/account/notifications"
EVENT_LABELS = {
    "new_request": "notifications.event.new_request",
    "new_response": "notifications.event.new_response",
    "chosen": "notifications.event.chosen",
    "request_cancelled": "notifications.event.request_cancelled",
    "review_prompt": "notifications.event.review_prompt",
    "expiry_reminder": "notifications.event.expiry_reminder",
    "done_check": "notifications.event.done_check",
}
CHANNEL_LABELS = {
    "telegram": "notifications.channel.telegram",
    "email": "notifications.channel.email",
}


@router.get(PAGE, name="notifications_page")
def notifications_page(
    request: Request, db: DbSession, user: CurrentUser, notice: str = ""
) -> Response:
    if user is None:
        return RedirectResponse(f"/signin?next={PAGE}", status_code=303)
    switches = notifications.prefs(db, user.id)
    return render(
        request,
        "pages/notifications.html",
        user=user,
        # Translation keys are chosen here, so every one is a literal (checked by the page tests).
        events=[(e, EVENT_LABELS[e]) for e in EVENTS],
        channels=[(c, CHANNEL_LABELS[c]) for c in CHANNELS],
        switches={f"{e}:{c}": on for (e, c), on in switches.items()},
        ready={c: notifications.channel_ready(user, c) for c in CHANNELS},
        bot_blocked=user.telegram_blocked_at is not None,
        saved=notice == "saved",
    )


@router.post(PAGE, name="notifications_save", dependencies=[CSRFProtected])
def notifications_save(
    db: DbSession,
    user: CurrentUser,
    on: Annotated[list[str], Form()] = [],  # noqa: B006  (FastAPI reads the default)
) -> Response:
    if user is None:
        return RedirectResponse(f"/signin?next={PAGE}", status_code=303)
    valid = {f"{e}:{c}": (e, c) for e in EVENTS for c in CHANNELS}
    # Unknown values are ignored; a switch left out of the form is off.
    notifications.save_prefs(db, user.id, {valid[v] for v in on if v in valid})
    return RedirectResponse(f"{PAGE}?notice=saved", status_code=303)
