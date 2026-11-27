"""The masters' feed (spec D3): open requests that match a visible master, newest first.

Who: only a master who is visible in search right now (`master_search.is_visible` plus the
owner-active and profile re-checks of `search._visible`), so an unpublished, hidden or blocked
master gets nothing even if the index is stale.
What: the request's specialties overlap the master's, and its place is in the master's work area
(district ∈ master districts, or the city is served whole, or the request has no district and
the city is one of the master's). Never hidden, never the master's own, never from a client whose
account is blocked or deleted.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, or_, select, tuple_
from sqlalchemy.orm import Session

from app.models import (
    JobRequest,
    JobResponse,
    MasterProfile,
    MasterSearch,
    RequestSpecialty,
    User,
)
from app.schemas.master import PhotoView
from app.schemas.requests import FeedCard, FeedPage
from app.services import requests as request_svc
from app.services import search
from app.services.storage import get_storage

PAGE_SIZE = 20

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICRO = timedelta(microseconds=1)
_CURSOR = re.compile(r"(\d{1,19})-(\d{1,19})")
_MAX_BIGINT = 2**63 - 1


@dataclass(frozen=True)
class EligibleMaster:
    """A master allowed to see the feed and respond, with their search arrays."""

    user: User
    profile: MasterProfile
    index: MasterSearch


def eligible_master(db: Session, user: User) -> EligibleMaster | None:
    """The user's master profile if it is visible in search right now, else None."""
    row = db.execute(
        search._visible(select(MasterProfile, MasterSearch).select_from(MasterSearch)).where(
            MasterProfile.user_id == user.id
        )
    ).first()
    if row is None:
        return None
    profile, index = row
    return EligibleMaster(user=user, profile=profile, index=index)


# --- matching ---------------------------------------------------------------------------------


def _matches(master: EligibleMaster) -> list[ColumnElement[bool]]:
    """Request conditions shared by the feed and by looking up one request for this master."""
    index = master.index
    return [
        exists().where(
            RequestSpecialty.request_id == JobRequest.id,
            RequestSpecialty.specialty_id.in_(index.specialty_ids or []),
        ),
        or_(
            JobRequest.district_id.in_(index.district_ids or []),
            JobRequest.city_id.in_(index.whole_city_ids or []),
            and_(JobRequest.district_id.is_(None), JobRequest.city_id.in_(index.city_ids or [])),
        ),
        JobRequest.hidden_at.is_(None),
        JobRequest.client_id != master.user.id,
        # The client's account must be active right now (a blocked client's requests vanish).
        exists().where(
            User.id == JobRequest.client_id, User.blocked_at.is_(None), User.deleted_at.is_(None)
        ),
    ]


def matching_request(
    db: Session, master: EligibleMaster, request_id: int, *, lock: bool = False
) -> JobRequest | None:
    """A request this master may see or answer, whatever its status or pause; else None.

    Status and pause are left to the caller so it can say "already closed" or "paused".
    """
    stmt = select(JobRequest).where(JobRequest.id == request_id, *_matches(master))
    if lock:
        stmt = stmt.with_for_update(of=JobRequest).execution_options(populate_existing=True)
    return db.scalar(stmt)


# --- cursor -----------------------------------------------------------------------------------


def make_cursor(created_at: datetime, request_id: int) -> str:
    return f"{(created_at - _EPOCH) // _MICRO}-{request_id}"


def parse_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    """(created_at, id) of the last card shown, or None for anything malformed (first page)."""
    found = _CURSOR.fullmatch(cursor or "")
    if found is None:
        return None
    micros, request_id = int(found.group(1)), int(found.group(2))
    if request_id > _MAX_BIGINT:
        return None
    try:
        return _EPOCH + micros * _MICRO, request_id
    except OverflowError:
        return None


# --- the feed ---------------------------------------------------------------------------------


def _card(
    db: Session, request: JobRequest, specialties: list[Any], photos: list[Any], lang: str
) -> FeedCard:
    storage = get_storage()
    return FeedCard(
        id=request.id,
        specialties=[request_svc._pick(lang, s) for s in specialties],
        place=request_svc._place(db, request, lang),
        description=request.description,
        budget_amount=request.budget_amount,
        budget_unit=request.budget_unit,
        timing=request.timing,
        timing_date=request.timing_date,
        created_at=request.created_at,
        photos=[
            PhotoView(
                id=p.id,
                url=storage.url(p.storage_key),
                thumb_url=storage.url(p.thumb_key),
                width=p.width,
                height=p.height,
                position=p.position,
                deleted=False,
            )
            for p in photos
        ],
    )


def feed(db: Session, user: User, cursor: str | None, *, lang: str = "ru") -> FeedPage:
    """One page of open, unpaused, matching requests the master has not (actively) answered."""
    master = eligible_master(db, user)
    if master is None:
        return FeedPage(eligible=False)
    stmt = select(JobRequest).where(
        *_matches(master),
        JobRequest.status == "open",
        JobRequest.feed_paused_at.is_(None),
        ~exists().where(
            JobResponse.request_id == JobRequest.id,
            JobResponse.master_id == master.profile.id,
            JobResponse.withdrawn_at.is_(None),
        ),
    )
    after = parse_cursor(cursor)
    if after is not None:
        stmt = stmt.where(tuple_(JobRequest.created_at, JobRequest.id) < tuple_(*after))
    rows = db.scalars(
        stmt.order_by(JobRequest.created_at.desc(), JobRequest.id.desc()).limit(PAGE_SIZE + 1)
    ).all()
    page = list(rows[:PAGE_SIZE])
    ids = [r.id for r in page]
    specialties, photos = request_svc._specialties(db, ids), request_svc._photos(db, ids)
    last = page[-1] if len(rows) > PAGE_SIZE else None
    return FeedPage(
        eligible=True,
        items=[_card(db, r, specialties[r.id], photos[r.id], lang) for r in page],
        next_cursor=make_cursor(last.created_at, last.id) if last is not None else None,
    )
