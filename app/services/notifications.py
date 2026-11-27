"""Notifications by Telegram and email (spec D8, D10.3, D10.6, D10.8; Review Focus 3).

Two steps, both worker jobs:
1. An event job (`new_request`, `new_response`, ...; payload ids only) decides who is told and
   queues one `notify` job per recipient and channel, each with its own dedupe key.
   `new_request` fans out to matching masters: at most 200 per request, and at most 20 requests
   per master per local (Asia/Dushanbe) day.
2. A `notify` job sends one message, so a failed Telegram send retries alone and never sends
   the email twice.

Every step re-reads the state when it runs and does nothing (the job is done) when the request
is hidden or gone, the recipient or the other side is blocked or deleted, or the event no longer
fits (a withdrawn response, a request closed meanwhile, ...). Handlers never commit: the job
runner commits under the job's row lock.

Messages carry the event, the specialties and place of the request and a link to its page;
never an address, a phone or an email of the other side.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.i18n import normalize_lang, translate
from app.core.time import LOCAL_TZ, utcnow
from app.models import JobRequest, JobResponse, MasterProfile, User
from app.models.jobs import CHANNELS, EVENTS, NOTIFY, Job, NotificationPref
from app.services import feed, jobs, telegram_send
from app.services import requests as request_svc
from app.services.auth import is_active
from app.services.email import EmailSender, OutgoingEmail, get_email_sender

log = logging.getLogger(__name__)

FANOUT_CAP = 200
DAILY_NEW_REQUEST_CAP = 20
# The object an event is about: the key part of the payload.
_OBJECT = {event: "request_id" for event in EVENTS} | {"new_response": "response_id"}
_DAILY_LOCK = text("SELECT pg_advisory_xact_lock(hashtext('notify_daily'), CAST(:k AS integer))")
# Closed by the client after choosing; an auto-close (C5) tells nobody.
_CLIENT_CLOSED = ("client_closed", "client_cancelled")
# Tests replace this; the worker uses the configured sender (log-only until A4).
email_sender: Callable[[], EmailSender] = get_email_sender


# --- preferences ------------------------------------------------------------------------------


def channel_ready(user: User, channel: str) -> bool:
    """The user can be reached there: Telegram linked and not blocked, or email confirmed."""
    if channel == "telegram":
        return user.telegram_id is not None and user.telegram_blocked_at is None
    return user.email is not None and user.email_verified_at is not None


def prefs(db: Session, user_id: int) -> dict[tuple[str, str], bool]:
    """Every (event, channel) switch of the user; a switch never saved is on."""
    saved = {
        (p.event, p.channel): p.enabled
        for p in db.scalars(select(NotificationPref).where(NotificationPref.user_id == user_id))
    }
    return {(e, c): saved.get((e, c), True) for e in EVENTS for c in CHANNELS}


def save_prefs(db: Session, user_id: int, enabled: set[tuple[str, str]]) -> None:
    """Store every switch of the user: on for the pairs in `enabled`, off for all others."""
    rows = [
        {"user_id": user_id, "event": e, "channel": c, "enabled": (e, c) in enabled}
        for e in EVENTS
        for c in CHANNELS
    ]
    stmt = insert(NotificationPref).values(rows)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "event", "channel"],
            set_={"enabled": stmt.excluded.enabled},
        )
    )


def _wants(db: Session, user: User, event: str) -> list[str]:
    """The channels this user can and wants to get `event` on."""
    switches = prefs(db, user.id)
    return [c for c in CHANNELS if channel_ready(user, c) and switches[(event, c)]]


# --- who is told: re-checked when each job runs -----------------------------------------------


@dataclass(frozen=True)
class Target:
    user: User
    request: JobRequest
    # Part of the dedupe key: what the message is about (one message per object and user).
    object_key: str


def _live_request(db: Session, request_id: Any) -> JobRequest | None:
    """The request if it is not hidden and its client is active, else None."""
    if not isinstance(request_id, int):
        return None
    request = db.get(JobRequest, request_id)
    if request is None or request.hidden_at is not None:
        return None
    client = db.get(User, request.client_id)
    if client is None or not is_active(client):
        return None
    return request


def _owner(db: Session, master_id: int | None) -> User | None:
    """The active owner of a master profile, else None."""
    profile = db.get(MasterProfile, master_id) if master_id is not None else None
    owner = db.get(User, profile.user_id) if profile is not None else None
    return owner if owner is not None and is_active(owner) else None


def _new_request_target(db: Session, request_id: Any, user_id: Any) -> Target | None:
    """A master still to be told about a request: it is open, unpaused and in their feed."""
    request = _live_request(db, request_id)
    if request is None or request.status != "open" or request.feed_paused_at is not None:
        return None
    user = db.get(User, user_id) if isinstance(user_id, int) else None
    master = feed.eligible_master(db, user) if user is not None else None
    if master is None or feed.matching_request(db, master, request.id) is None:
        return None
    answered = db.scalar(
        select(JobResponse.id).where(
            JobResponse.request_id == request.id,
            JobResponse.master_id == master.profile.id,
            JobResponse.withdrawn_at.is_(None),
        )
    )
    return None if answered is not None else Target(master.user, request, str(request.id))


def _target(db: Session, event: str, payload: dict[str, Any]) -> Target | None:
    """The one recipient of a single-recipient event, if it still fits; else None."""
    if event == "new_response":
        response_id = payload.get("response_id")
        response = db.get(JobResponse, response_id) if isinstance(response_id, int) else None
        if response is None or response.withdrawn_at is not None:
            return None
        request = _live_request(db, response.request_id)
        if request is None or request.status != "open":
            return None
        # The client's list shows only responses of available masters (D10.5).
        shown = db.scalar(
            request_svc._shown_master(select(JobResponse.id)).where(JobResponse.id == response.id)
        )
        if shown is None:
            return None
        client = db.get(User, request.client_id)
        assert client is not None  # noqa: S101  (checked by _live_request)
        return Target(client, request, str(response.id))
    request = _live_request(db, payload.get("request_id"))
    if request is None:
        return None
    chosen = _owner(db, request.chosen_master_id)
    client = db.get(User, request.client_id)
    assert client is not None  # noqa: S101  (checked by _live_request)
    status, key = request.status, str(request.id)
    if event == "expiry_reminder":
        if status != "open" or request.expiry_reminded_at is None:
            return None
        # Each open period (a reopen restarts it) gets its own reminder.
        return Target(client, request, f"{request.id}-{int(request.opened_at.timestamp())}")
    # The rest involve the chosen master; their account must be active too.
    if chosen is None:
        return None
    if event == "chosen" and status == "master_chosen":
        return Target(chosen, request, key)
    if event == "request_cancelled" and request.close_reason in _CLIENT_CLOSED:
        return Target(chosen, request, key)
    if event == "review_prompt" and status == "completed":
        return Target(client, request, key)
    if event == "done_check" and status == "master_chosen" and request.done_asked_at:
        return Target(client, request, key)
    return None


def _resolve(db: Session, event: str, payload: dict[str, Any]) -> Target | None:
    if event == "new_request":
        return _new_request_target(db, payload.get("request_id"), payload.get("user_id"))
    return _target(db, event, payload)


# --- queue the sends --------------------------------------------------------------------------


def _queue(db: Session, event: str, target: Target, channels: list[str]) -> None:
    obj = _OBJECT[event]
    obj_id = target.request.id if obj == "request_id" else int(target.object_key)
    for channel in channels:
        jobs.enqueue(
            db,
            NOTIFY,
            {"event": event, obj: obj_id, "user_id": target.user.id, "channel": channel},
            dedupe_key=f"notify:{event}:{target.object_key}:{target.user.id}:{channel}",
        )


def handle_event(event: str) -> jobs.Handler:
    """The handler of a single-recipient event job."""

    def handler(db: Session, payload: dict[str, Any]) -> None:
        target = _target(db, event, payload)
        if target is not None:
            _queue(db, event, target, _wants(db, target.user, event))

    return handler


def local_day(now: datetime) -> tuple[datetime, datetime]:
    """Start and end (UTC) of the Asia/Dushanbe calendar day `now` falls in."""
    day = now.astimezone(LOCAL_TZ).date()
    start = datetime.combine(day, time(), tzinfo=LOCAL_TZ).astimezone(UTC)
    return start, start + timedelta(days=1)


def _queued_today(db: Session, user_ids: list[int], now: datetime) -> dict[int, int]:
    """How many requests each master was queued new-request notifications for today."""
    start, end = local_day(now)
    user_id = Job.payload["user_id"].as_integer()
    rows = db.execute(
        select(user_id, func.count(Job.payload["request_id"].as_integer().distinct()))
        .where(
            Job.kind == NOTIFY,
            Job.payload["event"].as_string() == "new_request",
            user_id.in_(user_ids),
            Job.created_at >= start,
            Job.created_at < end,
        )
        .group_by(user_id)
    ).all()
    return {uid: count for uid, count in rows}


def fan_out_new_request(
    db: Session, payload: dict[str, Any], *, now: datetime | None = None
) -> None:
    """Queue new-request notifications for the matching masters (spec D8)."""
    request = _live_request(db, payload.get("request_id"))
    if request is None or request.status != "open" or request.feed_paused_at is not None:
        return
    masters = feed.matching_masters(db, request, limit=FANOUT_CAP)
    wanted = [(m, _wants(db, m, "new_request")) for m in masters]
    wanted = sorted(((m, c) for m, c in wanted if c), key=lambda pair: pair[0].id)
    if not wanted:
        return
    # Two fan-outs at once must not both see 19 and make it 21: lock each master's count
    # (ascending ids, so two fan-outs never deadlock); released at commit.
    for master, _ in wanted:
        db.execute(_DAILY_LOCK, {"k": master.id % 2**31})
    counts = _queued_today(db, [m.id for m, _ in wanted], now or utcnow())
    for master, channels in wanted:
        if counts.get(master.id, 0) >= DAILY_NEW_REQUEST_CAP:
            continue  # they still see it in the feed
        _queue(db, "new_request", Target(master, request, str(request.id)), channels)


# --- send one message -------------------------------------------------------------------------


def render(event: str, lang: str, request: JobRequest, db: Session) -> tuple[str, str]:
    """(subject, text) of the message: what happened, which request, and its link."""
    specialties = request_svc._specialties(db, [request.id])[request.id]
    summary = ", ".join(request_svc._pick(lang, s) for s in specialties)
    summary = f"{summary} · {request_svc._place(db, request, lang)}"
    site = get_settings().site_url.rstrip("/")
    body = "\n\n".join(
        (
            translate(lang, f"notify.{event}", summary=summary),
            f"{site}/requests/{request.id}",
            translate(lang, "notify.footer", link=f"{site}/account/notifications"),
        )
    )
    return translate(lang, f"notify.{event}.subject"), body


def handle_notify(db: Session, payload: dict[str, Any]) -> None:
    """Send one message, if the event still fits and the user still wants it there."""
    event, channel, user_id = payload.get("event"), payload.get("channel"), payload.get("user_id")
    if event not in EVENTS or channel not in CHANNELS:
        log.warning("notify job with an unknown event or channel skipped")
        return
    target = _resolve(db, event, payload)
    if target is None or target.user.id != user_id:
        return
    user = target.user
    if not channel_ready(user, channel) or not prefs(db, user.id)[(event, channel)]:
        return
    subject, body = render(event, normalize_lang(user.locale), target.request, db)
    if channel == "email":
        assert user.email is not None  # noqa: S101  (channel_ready)
        email_sender().send(OutgoingEmail(to=user.email, subject=subject, body=body))
        return
    assert user.telegram_id is not None  # noqa: S101  (channel_ready)
    outcome = telegram_send.send_message(user.telegram_id, body)
    if outcome is telegram_send.Delivery.BLOCKED:
        # No more Telegram for this user until they write to the bot again (spec D10.6).
        db.execute(
            update(User)
            .where(User.id == user.id, User.telegram_blocked_at.is_(None))
            .values(telegram_blocked_at=utcnow())
        )


def install() -> None:
    """Register the notification handlers with the job runner."""
    jobs.register("new_request", fan_out_new_request)
    for event in EVENTS:
        if event != "new_request":
            jobs.register(event, handle_event(event))
    jobs.register(NOTIFY, handle_notify)
