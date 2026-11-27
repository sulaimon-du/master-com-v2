"""The master's side of a request: respond ("Ready to take it"), withdraw, and "My responses".

Respond needs a master who is visible right now and a request that matches them (the feed's
rules, `feed.matching_request`); anything else is "not found", so an id from a URL reveals
nothing. Withdraw and "My responses" go through the master's own response rows.
Lock order: the request row, then the response row. Callers commit.
"""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import JobRequest, JobResponse, MasterProfile, User
from app.schemas.requests import MyResponseCard, MyResponseStatus, ResponseInput
from app.services import feed, jobs
from app.services import requests as request_svc
from app.services.auth import is_active
from app.services.masters import master_for_user
from app.services.storage import get_storage

ResponseCode = Literal[
    "inactive",  # the user's account is blocked or deleted
    "not_master",  # no visible master profile (unpublished, hidden, or not a master)
    "not_found",  # no such request for this master (foreign, own, hidden, not matching)
    "message",
    "price",
    "already",  # an active response exists (a double submit)
    "state",  # the request is no longer open
    "paused",  # the request left the feed after its 15 responses
    "withdrawn",  # already withdrawn (a double submit)
    "chosen",  # the chosen master cannot withdraw
]

MAX_MESSAGE = 500


class ResponseError(Exception):
    """A rule was broken; `code` says which, so the page can show the matching message."""

    def __init__(self, code: ResponseCode) -> None:
        super().__init__(code)
        self.code = code


def _require_active(user: User) -> None:
    if not is_active(user):
        raise ResponseError("inactive")


def _clean(data: ResponseInput) -> str | None:
    message = (data.message or "").strip() or None
    if message is not None and len(message) > MAX_MESSAGE:
        raise ResponseError("message")
    if not request_svc.money_ok(data.price_amount, data.price_unit):
        raise ResponseError("price")
    return message


def respond(db: Session, user: User, request_id: int, data: ResponseInput) -> JobResponse:
    """Answer an open, unpaused request in the master's feed; one row per master and request.

    Responding again after a withdrawal revives the same row with the new message and price.
    The response that reaches the pause threshold takes the request out of the feed.
    """
    _require_active(user)
    master = feed.eligible_master(db, user)
    if master is None:
        raise ResponseError("not_master")
    message = _clean(data)
    # Two tabs submitting at once serialise here; the second then sees the first's row.
    request = feed.matching_request(db, master, request_id, lock=True)
    if request is None:
        raise ResponseError("not_found")
    response = db.scalar(
        select(JobResponse)
        .where(JobResponse.request_id == request.id, JobResponse.master_id == master.profile.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if response is not None and response.withdrawn_at is None:
        raise ResponseError("already")
    if request.status != "open":
        raise ResponseError("state")
    if request.feed_paused_at is not None:
        raise ResponseError("paused")
    if response is None:
        response = JobResponse(request_id=request.id, master_id=master.profile.id)
        db.add(response)
    response.message = message
    response.price_amount = data.price_amount
    response.price_unit = data.price_unit if data.price_amount is not None else None
    response.withdrawn_at = None
    request.responses_count += 1
    if request.responses_count >= request.feed_pause_at and request.feed_paused_at is None:
        request.feed_paused_at = utcnow()
    db.flush()
    # One notification per master and request, however often they withdraw and come back.
    jobs.enqueue(
        db, "new_response", {"response_id": response.id}, dedupe_key=f"new_response:{response.id}"
    )
    return response


def withdraw(db: Session, user: User, request_id: int) -> JobResponse:
    """Withdraw the master's response unless they are the chosen one (spec D5, D10.2).

    Allowed whether or not the profile is still published: existing responses stay theirs.
    Withdrawing never puts a paused request back in the feed.
    """
    _require_active(user)
    profile = master_for_user(db, user)
    if profile is None:
        raise ResponseError("not_found")

    def own_response(*, lock: bool) -> JobResponse | None:
        stmt = select(JobResponse).where(
            JobResponse.request_id == request_id, JobResponse.master_id == profile.id
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return db.scalar(stmt)

    if own_response(lock=False) is None:  # never lock a request row the master has no part in
        raise ResponseError("not_found")
    request = db.scalar(
        select(JobRequest)
        .where(JobRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    response = own_response(lock=True)
    if request is None or response is None:
        raise ResponseError("not_found")
    if response.withdrawn_at is not None:
        raise ResponseError("withdrawn")
    if request.chosen_master_id == profile.id:
        raise ResponseError("chosen")
    response.withdrawn_at = utcnow()
    request.responses_count -= 1
    db.flush()
    return response


# --- my responses -----------------------------------------------------------------------------


def _status(request: JobRequest, client: User, profile: MasterProfile) -> MyResponseStatus:
    if request.hidden_at is not None or not is_active(client):
        return "unavailable"
    if request.status in ("closed", "cancelled"):
        return request.status  # type: ignore[return-value]
    if request.chosen_master_id is None:
        return "open"
    return "chosen" if request.chosen_master_id == profile.id else "other_chosen"


def my_responses(db: Session, user: User, *, lang: str = "ru") -> list[MyResponseCard]:
    """The master's active responses, newest first, each with the request's state for them."""
    profile = master_for_user(db, user)
    if profile is None:
        return []
    rows = db.execute(
        select(JobResponse, JobRequest, User)
        .join(JobRequest, JobRequest.id == JobResponse.request_id)
        .join(User, User.id == JobRequest.client_id)
        .where(JobResponse.master_id == profile.id, JobResponse.withdrawn_at.is_(None))
        .order_by(JobResponse.updated_at.desc(), JobResponse.id.desc())
    ).all()
    shown = [r.id for _, r, client in rows if _status(r, client, profile) != "unavailable"]
    specialties, photos = request_svc._specialties(db, shown), request_svc._photos(db, shown)
    storage = get_storage()
    cards: list[MyResponseCard] = []
    for response, request, client in rows:
        status = _status(request, client, profile)
        card = MyResponseCard(
            request_id=request.id,
            status=status,
            responded_at=response.updated_at,  # a re-respond shows its own date
            message=response.message,
            price_amount=response.price_amount,
            price_unit=response.price_unit,
        )
        if status != "unavailable":  # "no longer available": no details at all (spec D10.9)
            card.specialties = [request_svc._pick(lang, s) for s in specialties[request.id]]
            card.place = request_svc._place(db, request, lang)
            card.description = request.description
            first = photos[request.id][:1]
            card.thumb_url = storage.url(first[0].thumb_key) if first else None
        cards.append(card)
    return cards
