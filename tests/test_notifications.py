"""Notifications (spec D8, D10.3, D10.6, D10.8; Review Focus 3).

Handlers run in the rolled-back `db` session; Telegram goes to a fake transport (no network) and
email to a recording sender.
"""

import http.client
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import LOCAL_TZ, utcnow
from app.models import District, Job, JobRequest, User
from app.models.jobs import CHANNELS, EVENTS, NOTIFY
from app.schemas.requests import ResponseInput
from app.services import feed, jobs, notifications, telegram_send
from app.services import requests as request_svc
from app.services import responses as response_svc
from app.services.jobs import RetryAfter
from app.services.telegram_send import Delivery
from app.worker import loop
from tests import feed_support as fs

TOKEN = "123456:SECRET-test-token"
SITE = "https://ustoiman.example"
ADDRESS = "улица Тайная, дом 77"
CLIENT_PHONE = "+992900000077"


class FakeTelegram:
    """Records sendMessage calls; answers `answer` or raises `raises`."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.answer: tuple[int, bytes] = (200, b'{"ok": true}')
        self.raises: Exception | None = None

    def __call__(self, path: str, body: bytes) -> tuple[int, bytes]:
        self.sent.append((path, json.loads(body)))
        if self.raises is not None:
            raise self.raises
        return self.answer


class Mailbox:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def send(self, message: Any) -> None:
        self.sent.append(message)


@pytest.fixture
def tg(monkeypatch) -> FakeTelegram:  # type: ignore[no-untyped-def]
    fake = FakeTelegram()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("SITE_URL", SITE)
    get_settings.cache_clear()
    monkeypatch.setattr(telegram_send, "transport", fake)
    return fake


@pytest.fixture
def mail(monkeypatch) -> Mailbox:  # type: ignore[no-untyped-def]
    box = Mailbox()
    monkeypatch.setattr(notifications, "email_sender", lambda: box)
    return box


def reachable(user: User, *, telegram: bool = True, email: bool = True) -> User:
    if telegram:
        user.telegram_id = uuid.uuid4().int % 10**12
    if email:
        user.email_verified_at = utcnow()
    return user


def _notify_jobs(db: Session, *, event: str | None = None, user: User | None = None) -> list[Job]:
    rows = db.scalars(select(Job).where(Job.kind == NOTIFY).order_by(Job.id)).all()
    return [
        j
        for j in rows
        if (event is None or j.payload["event"] == event)
        and (user is None or j.payload["user_id"] == user.id)
    ]


def _channels(db: Session, user: User, event: str, request_id: int | None = None) -> list[str]:
    return sorted(
        j.payload["channel"]
        for j in _notify_jobs(db, event=event, user=user)
        if request_id is None or j.payload.get("request_id") == request_id
    )


def _send_all(db: Session, user: User) -> None:
    for job in _notify_jobs(db, user=user):
        notifications.handle_notify(db, job.payload)


def _client(db: Session) -> User:
    client = reachable(fs.user(db, name="Дилноза"))
    client.phone, client.phone_verified_at = CLIENT_PHONE, utcnow()
    db.flush()
    return client


@pytest.fixture
def town(db: Session):  # type: ignore[no-untyped-def]
    fs.hide_other_requests(db)
    return fs.city_without_districts(db)


@pytest.fixture
def scene(db: Session, town):  # type: ignore[no-untyped-def]
    """A reachable client's request with an address, a reachable matching master."""
    client = _client(db)
    owner, profile = fs.master(db, city_ids=(town.id,))
    reachable(owner)
    request = fs.post(db, client, city_id=town.id, address=ADDRESS)
    db.flush()
    return client, owner, profile, request


def _assert_private(text: str, *users: User) -> None:
    assert ADDRESS not in text and CLIENT_PHONE not in text
    for u in users:
        assert u.email not in text
        if u.phone:
            assert u.phone not in text


# --- new_request fan-out ----------------------------------------------------------------------


def test_new_request_fans_out_to_matching_masters_only(db: Session, town, scene) -> None:
    client, owner, _, request = scene
    other_spec, _ = fs.master(db, specs=("elektrik",), city_ids=(town.id,))
    unpublished, _ = fs.master(db, city_ids=(town.id,), ready=False)
    blocked, _ = fs.master(db, city_ids=(town.id,))
    silent, _ = fs.master(db, city_ids=(town.id,))  # no Telegram, no confirmed email
    for u in (other_spec, unpublished, blocked):
        reachable(u)
    blocked.blocked_at = utcnow()
    db.flush()
    notifications.fan_out_new_request(db, {"request_id": request.id})
    assert _channels(db, owner, "new_request") == ["email", "telegram"]
    for u in (other_spec, unpublished, blocked, silent, client):
        assert _notify_jobs(db, user=u) == []
    job = _notify_jobs(db, user=owner)[0]
    assert job.dedupe_key == f"notify:new_request:{request.id}:{owner.id}:{job.payload['channel']}"
    # Running the fan-out again queues nothing new.
    notifications.fan_out_new_request(db, {"request_id": request.id})
    assert len(_notify_jobs(db, user=owner)) == 2


@pytest.mark.parametrize(
    "change", ["hidden", "client_blocked", "client_deleted", "paused", "closed"]
)
def test_no_fan_out_for_a_request_that_no_longer_fits(db: Session, scene, change: str) -> None:
    client, owner, _, request = scene
    if change == "hidden":
        request.hidden_at = utcnow()
    elif change == "client_blocked":
        client.blocked_at = utcnow()
    elif change == "client_deleted":
        client.deleted_at = utcnow()
    elif change == "paused":
        request.feed_paused_at = utcnow()
    else:
        request_svc.close_request(db, client, request.id, "closed")
    db.flush()
    notifications.fan_out_new_request(db, {"request_id": request.id})
    assert _notify_jobs(db, user=owner) == []


def test_new_request_message_has_request_info_and_link_but_no_private_data(
    db: Session, scene, tg: FakeTelegram, mail: Mailbox
) -> None:
    client, owner, _, request = scene
    notifications.fan_out_new_request(db, {"request_id": request.id})
    _send_all(db, owner)
    ((path, sent),) = tg.sent
    assert path == f"/bot{TOKEN}/sendMessage"
    assert sent["chat_id"] == owner.telegram_id
    assert "Сантехник" in sent["text"] and request_svc._place(db, request, "ru") in sent["text"]
    assert f"{SITE}/requests/{request.id}" in sent["text"]
    _assert_private(sent["text"], client)
    assert client.name not in sent["text"]
    ((email,),) = [mail.sent]
    assert email.to == owner.email and email.body == sent["text"]
    _assert_private(email.subject + email.body, client)


def test_messages_use_the_recipients_language(db: Session, scene, tg: FakeTelegram) -> None:
    _, owner, _, request = scene
    owner.locale = "tg"
    owner.email_verified_at = None
    db.flush()
    notifications.fan_out_new_request(db, {"request_id": request.id})
    _send_all(db, owner)
    assert tg.sent[0][1]["text"].startswith("Дархости нав")


@pytest.mark.parametrize("change", ["responded", "unpublished", "blocked", "request_hidden"])
def test_a_queued_new_request_is_dropped_when_it_stopped_fitting(
    db: Session, scene, tg: FakeTelegram, mail: Mailbox, change: str
) -> None:
    _, owner, profile, request = scene
    notifications.fan_out_new_request(db, {"request_id": request.id})
    if change == "responded":
        response_svc.respond(db, owner, request.id, ResponseInput())
    elif change == "unpublished":
        profile.hidden_at = utcnow()
    elif change == "blocked":
        owner.blocked_at = utcnow()
    else:
        request.hidden_at = utcnow()
    db.flush()
    _send_all(db, owner)
    assert tg.sent == [] and mail.sent == []


def test_fan_out_is_capped_per_request_newest_masters_first(
    db: Session, town, scene, monkeypatch
) -> None:
    _, first, _, request = scene
    later = [reachable(fs.master(db, city_ids=(town.id,))[0]) for _ in range(2)]
    db.flush()
    monkeypatch.setattr(notifications, "FANOUT_CAP", 2)
    notifications.fan_out_new_request(db, {"request_id": request.id})
    told = {j.payload["user_id"] for j in _notify_jobs(db, event="new_request")}
    assert told == {u.id for u in later}
    assert first.id not in told


def test_unreachable_or_opted_out_masters_never_crowd_out_a_reachable_one(
    db: Session, town, scene
) -> None:
    _, older, _, request = scene  # reachable, published first
    newer = [fs.master(db, city_ids=(town.id,))[0] for _ in range(notifications.FANOUT_CAP)]
    # A few are linked but opted out of new requests on every channel they have.
    for u in newer[:3]:
        reachable(u)
        notifications.save_prefs(
            db, u.id, {(e, c) for e in EVENTS for c in CHANNELS if e != "new_request"}
        )
    newer[3].telegram_id, newer[3].telegram_blocked_at = 777000111, utcnow()  # bot blocked
    db.flush()
    notifications.fan_out_new_request(db, {"request_id": request.id})
    assert _channels(db, older, "new_request") == ["email", "telegram"]
    assert all(_notify_jobs(db, user=u) == [] for u in newer)


def test_an_opt_out_on_one_channel_keeps_the_other(db: Session, scene) -> None:
    _, owner, _, request = scene
    everything = {(e, c) for e in EVENTS for c in CHANNELS}
    notifications.save_prefs(db, owner.id, everything - {("new_request", "email")})
    notifications.fan_out_new_request(db, {"request_id": request.id})
    assert _channels(db, owner, "new_request") == ["telegram"]


def _old_notifications(db: Session, user: User, n: int, at: datetime) -> None:
    db.add_all(
        Job(
            kind=NOTIFY,
            payload={
                "event": "new_request",
                "request_id": -1 - i,
                "user_id": user.id,
                "channel": "email",
            },
            dedupe_key=f"test-old:{user.id}:{i}:{uuid.uuid4().hex[:6]}",
            created_at=at,
            done_at=at,
        )
        for i in range(n)
    )
    db.flush()


# Shortly after local midnight: 00:00:30 in Dushanbe is 19:00:30 UTC the day before.
LOCAL_MIDNIGHT = datetime(2026, 11, 27, tzinfo=LOCAL_TZ)
NOW = (LOCAL_MIDNIGHT + timedelta(seconds=30)).astimezone(UTC)


@pytest.mark.parametrize(
    ("count", "at", "told"),
    [
        (20, LOCAL_MIDNIGHT, False),  # 20 already today: no 21st
        (19, LOCAL_MIDNIGHT, True),  # the 20th is fine
        (20, LOCAL_MIDNIGHT - timedelta(seconds=1), True),  # yesterday, local time
    ],
)
def test_at_most_20_new_requests_per_master_per_local_day(
    db: Session, scene, count: int, at: datetime, told: bool
) -> None:
    _, owner, _, request = scene
    _old_notifications(db, owner, count, at)
    notifications.fan_out_new_request(db, {"request_id": request.id}, now=NOW)
    assert bool(_channels(db, owner, "new_request", request.id)) is told


def test_both_channels_of_one_request_count_once_toward_the_daily_cap(db: Session, scene) -> None:
    _, owner, _, request = scene
    _old_notifications(db, owner, 19, LOCAL_MIDNIGHT)
    # The same 19 requests on Telegram too: still 19 requests.
    for job in _notify_jobs(db, user=owner):
        db.add(
            Job(
                kind=NOTIFY,
                payload={**job.payload, "channel": "telegram"},
                dedupe_key=job.dedupe_key + ":tg",
                created_at=job.created_at,
            )
        )
    db.flush()
    notifications.fan_out_new_request(db, {"request_id": request.id}, now=NOW)
    assert _channels(db, owner, "new_request", request.id) == ["email", "telegram"]


def test_local_day_is_the_dushanbe_calendar_day() -> None:
    start, end = notifications.local_day(NOW)
    assert start == datetime(2026, 11, 26, 19, 0, tzinfo=UTC)
    assert end - start == timedelta(days=1)


def test_matching_masters_is_the_feed_rule_seen_from_the_request(db: Session) -> None:
    fs.hide_other_requests(db)
    city = fs.city(db, "dushanbe")
    districts = db.scalars(
        select(District)
        .where(District.city_id == city.id, District.is_active)
        .order_by(District.id)
        .limit(2)
    ).all()
    town = fs.city_without_districts(db)
    masters = [
        fs.master(db, cities=("dushanbe",)),  # whole city
        fs.master(db, city_ids=(town.id,)),
        fs.master(db, specs=("elektrik",), cities=("dushanbe",)),
        fs.master(db, specs=("santekhnik", "elektrik"), city_ids=(town.id,)),
    ]
    if districts:
        masters.append(fs.master(db, districts=(("dushanbe", districts[0].slug),)))
    requests = [
        fs.post(db, city_id=town.id),
        fs.post(db, specs=("elektrik",), city_id=town.id),
        fs.post(db, specs=("elektrik",), city_id=city.id, district_id=districts[0].id)
        if districts
        else fs.post(db, specs=("elektrik",), city_id=town.id),
    ]
    if len(districts) > 1:
        requests.append(fs.post(db, city_id=city.id, district_id=districts[1].id))
    ours = {u.id for u, _ in masters}
    for request in requests:
        found = {u.id for u in feed.matching_masters(db, request, limit=10_000)} & ours
        expected = {
            u.id
            for u, _ in masters
            if feed.matching_request(db, feed.eligible_master(db, u), request.id) is not None
        }
        assert found == expected, request.id


# --- single-recipient events ------------------------------------------------------------------


def _respond(db: Session, owner: User, request: JobRequest):  # type: ignore[no-untyped-def]
    return response_svc.respond(db, owner, request.id, ResponseInput(message="Сделаю завтра"))


def _choose(db: Session, client: User, owner: User, request: JobRequest) -> None:
    response = _respond(db, owner, request)
    request_svc.choose_master(db, client, request.id, response.id, CLIENT_PHONE)


def _event(db: Session, event: str, payload: dict[str, Any]) -> None:
    notifications.handle_event(event)(db, payload)


def test_new_response_tells_the_client_without_the_masters_contacts(
    db: Session, scene, tg: FakeTelegram, mail: Mailbox
) -> None:
    client, owner, _, request = scene
    response = _respond(db, owner, request)
    _event(db, "new_response", {"response_id": response.id})
    assert _channels(db, client, "new_response") == ["email", "telegram"]
    assert _notify_jobs(db, user=owner) == []
    _send_all(db, client)
    text = tg.sent[0][1]["text"]
    assert tg.sent[0][1]["chat_id"] == client.telegram_id
    assert f"/requests/{request.id}" in text
    _assert_private(text, owner)
    assert mail.sent[0].to == client.email


@pytest.mark.parametrize("change", ["withdrawn", "master_blocked", "master_hidden", "chosen"])
def test_new_response_is_dropped_when_it_stopped_fitting(
    db: Session, town, scene, tg: FakeTelegram, mail: Mailbox, change: str
) -> None:
    client, owner, profile, request = scene
    response = _respond(db, owner, request)
    _event(db, "new_response", {"response_id": response.id})
    if change == "withdrawn":
        response_svc.withdraw(db, owner, request.id)
    elif change == "master_blocked":
        owner.blocked_at = utcnow()
    elif change == "master_hidden":
        profile.hidden_at = utcnow()
    else:
        another, _ = fs.master(db, city_ids=(town.id,))
        _choose(db, client, another, request)
    db.flush()
    _send_all(db, client)
    assert tg.sent == [] and mail.sent == []
    # Nor is anything queued when the event job runs after the change.
    before = len(_notify_jobs(db))
    _event(db, "new_response", {"response_id": response.id})
    assert len(_notify_jobs(db)) == before


def test_chosen_tells_the_chosen_master_only(db: Session, town, scene, tg: FakeTelegram) -> None:
    client, owner, _, request = scene
    other = reachable(fs.master(db, city_ids=(town.id,))[0])
    _respond(db, other, request)
    _choose(db, client, owner, request)
    _event(db, "chosen", {"request_id": request.id})
    assert _channels(db, owner, "chosen") == ["email", "telegram"]
    assert _notify_jobs(db, event="chosen", user=other) == []
    owner.email_verified_at = None
    db.flush()
    _send_all(db, owner)
    text = tg.sent[0][1]["text"]
    # Even the chosen master gets the phone on the page only, never in a message.
    _assert_private(text, client)


@pytest.mark.parametrize("change", ["closed", "client_blocked", "master_blocked", "hidden"])
def test_chosen_is_dropped_when_it_stopped_fitting(
    db: Session, scene, tg: FakeTelegram, mail: Mailbox, change: str
) -> None:
    client, owner, _, request = scene
    _choose(db, client, owner, request)
    _event(db, "chosen", {"request_id": request.id})
    if change == "closed":
        request_svc.close_request(db, client, request.id, "cancelled")
    elif change == "client_blocked":
        client.blocked_at = utcnow()
    elif change == "master_blocked":
        owner.blocked_at = utcnow()
    else:
        request.hidden_at = utcnow()
    db.flush()
    _send_all(db, owner)
    assert tg.sent == [] and mail.sent == []


@pytest.mark.parametrize("reason", ["closed", "cancelled"])
def test_request_cancelled_tells_the_chosen_master(
    db: Session, scene, tg: FakeTelegram, reason: str
) -> None:
    client, owner, _, request = scene
    _choose(db, client, owner, request)
    request_svc.close_request(db, client, request.id, reason)  # type: ignore[arg-type]
    _event(db, "request_cancelled", {"request_id": request.id})
    assert _channels(db, owner, "request_cancelled") == ["email", "telegram"]
    assert _notify_jobs(db, event="request_cancelled", user=client) == []


def test_request_cancelled_is_not_sent_after_an_auto_close(db: Session, scene) -> None:
    client, owner, _, request = scene
    _choose(db, client, owner, request)
    request.status, request.close_reason, request.closed_at = "closed", "auto_stale", utcnow()
    db.flush()
    _event(db, "request_cancelled", {"request_id": request.id})
    assert _notify_jobs(db) == []


def test_review_prompt_and_done_check_tell_the_client(db: Session, scene) -> None:
    client, owner, _, request = scene
    _choose(db, client, owner, request)
    request.done_asked_at = utcnow()
    db.flush()
    _event(db, "done_check", {"request_id": request.id})
    assert _channels(db, client, "done_check") == ["email", "telegram"]
    request_svc.complete_request(db, client, request.id)
    _event(db, "review_prompt", {"request_id": request.id})
    assert _channels(db, client, "review_prompt") == ["email", "telegram"]
    assert _notify_jobs(db, user=owner) == []
    # done_check after completion no longer fits.
    for job in _notify_jobs(db, event="done_check"):
        assert notifications._resolve(db, "done_check", job.payload) is None


def test_review_prompt_is_skipped_when_the_master_was_blocked(db: Session, scene) -> None:
    client, owner, _, request = scene
    _choose(db, client, owner, request)
    request_svc.complete_request(db, client, request.id)
    owner.blocked_at = utcnow()
    db.flush()
    _event(db, "review_prompt", {"request_id": request.id})
    assert _notify_jobs(db) == []


def test_expiry_reminder_tells_the_client_once_per_open_period(db: Session, scene) -> None:
    client, _, _, request = scene
    request.expiry_reminded_at = utcnow()
    db.flush()
    _event(db, "expiry_reminder", {"request_id": request.id})
    first = _notify_jobs(db, event="expiry_reminder", user=client)
    assert sorted(j.payload["channel"] for j in first) == ["email", "telegram"]
    # A reopen starts a new period with its own reminder.
    request.opened_at = utcnow() + timedelta(seconds=5)
    db.flush()
    _event(db, "expiry_reminder", {"request_id": request.id})
    assert len(_notify_jobs(db, event="expiry_reminder", user=client)) == 4


def test_expiry_reminder_is_skipped_when_the_request_is_not_open(db: Session, scene) -> None:
    client, _, _, request = scene
    request.expiry_reminded_at = utcnow()
    request_svc.close_request(db, client, request.id, "closed")
    _event(db, "expiry_reminder", {"request_id": request.id})
    assert _notify_jobs(db) == []


def test_unknown_or_malformed_payloads_do_nothing(db: Session, tg: FakeTelegram) -> None:
    for payload in ({}, {"request_id": "1"}, {"request_id": 2**62}):
        notifications.fan_out_new_request(db, payload)
        for event in EVENTS:
            if event != "new_request":
                _event(db, event, payload)
    notifications.handle_notify(db, {"event": "nope", "channel": "telegram", "user_id": 1})
    notifications.handle_notify(db, {"event": "chosen", "channel": "pigeon", "user_id": 1})
    assert _notify_jobs(db) == [] and tg.sent == []


def test_a_notify_job_for_someone_else_is_not_sent(db: Session, scene, tg: FakeTelegram) -> None:
    client, owner, _, request = scene
    _choose(db, client, owner, request)
    payload = {
        "event": "chosen",
        "request_id": request.id,
        "user_id": client.id,
        "channel": "telegram",
    }
    notifications.handle_notify(db, payload)
    assert tg.sent == []


# --- preferences ------------------------------------------------------------------------------


def test_prefs_default_on_and_are_honoured_per_channel(
    db: Session, scene, tg: FakeTelegram, mail: Mailbox
) -> None:
    _, owner, _, request = scene
    assert all(notifications.prefs(db, owner.id).values())
    everything = {(e, c) for e in EVENTS for c in CHANNELS}
    notifications.save_prefs(db, owner.id, everything - {("new_request", "telegram")})
    notifications.fan_out_new_request(db, {"request_id": request.id})
    assert _channels(db, owner, "new_request") == ["email"]
    # Switched off after queueing: not sent either.
    notifications.save_prefs(db, owner.id, everything - {("new_request", "email")})
    _send_all(db, owner)
    assert mail.sent == [] and tg.sent == []


def test_save_prefs_overwrites_the_users_own_switches_only(db: Session) -> None:
    a, b = fs.user(db), fs.user(db)
    notifications.save_prefs(db, a.id, set())
    notifications.save_prefs(db, a.id, {("chosen", "email")})
    mine = notifications.prefs(db, a.id)
    assert [k for k, on in mine.items() if on] == [("chosen", "email")]
    assert all(notifications.prefs(db, b.id).values())


def test_users_without_a_channel_just_skip_it(db: Session, scene, mail: Mailbox) -> None:
    _, owner, _, request = scene
    owner.telegram_id = None
    db.flush()
    notifications.fan_out_new_request(db, {"request_id": request.id})
    assert _channels(db, owner, "new_request") == ["email"]


# --- Telegram delivery ------------------------------------------------------------------------


def test_403_marks_the_bot_blocked_and_stops_telegram(
    db: Session, scene, tg: FakeTelegram, mail: Mailbox
) -> None:
    client, owner, _, request = scene
    tg.answer = (403, b'{"ok": false, "error_code": 403}')
    notifications.fan_out_new_request(db, {"request_id": request.id})
    _send_all(db, owner)  # no exception: the job is done, not retried
    db.refresh(owner)
    assert owner.telegram_blocked_at is not None
    assert len(tg.sent) == 1 and len(mail.sent) == 1
    # From now on only email for this user.
    response = _respond(db, owner, fs.post(db, client, city_id=request.city_id))
    _event(db, "new_response", {"response_id": response.id})
    tg.sent.clear()
    _send_all(db, client)
    assert tg.sent[0][1]["chat_id"] == client.telegram_id
    second = fs.post(db, city_id=request.city_id)
    notifications.fan_out_new_request(db, {"request_id": second.id})
    assert sorted(
        j.payload["channel"]
        for j in _notify_jobs(db, event="new_request", user=owner)
        if j.payload["request_id"] == second.id
    ) == ["email"]


def test_send_message_delivery_semantics(tg: FakeTelegram) -> None:
    assert telegram_send.send_message(1, "hi") is Delivery.SENT
    tg.answer = (403, b"{}")
    assert telegram_send.send_message(1, "hi") is Delivery.BLOCKED
    tg.answer = (400, b'{"description": "chat not found"}')
    assert telegram_send.send_message(1, "hi") is Delivery.REJECTED
    tg.answer = (429, b'{"ok": false, "parameters": {"retry_after": 37}}')
    with pytest.raises(RetryAfter) as limited:
        telegram_send.send_message(1, "hi")
    assert limited.value.seconds == 37
    tg.answer = (502, b"")
    with pytest.raises(telegram_send.TelegramUnavailable):
        telegram_send.send_message(1, "hi")
    tg.raises = telegram_send.NotSent()  # connection refused, DNS, connect timeout
    with pytest.raises(telegram_send.TelegramUnavailable) as down:
        telegram_send.send_message(1, "hi")
    assert str(down.value) == "" and down.value.__cause__ is None
    tg.raises = telegram_send.NoAnswer()  # sent, then the read timed out: delivered
    assert telegram_send.send_message(1, "hi") is Delivery.SENT


@pytest.mark.parametrize("status", [401, 404])
def test_a_rejected_token_fails_loud_without_leaking_it(  # type: ignore[no-untyped-def]
    tg: FakeTelegram, caplog, status: int
) -> None:
    tg.answer = (status, b'{"ok": false, "description": "Unauthorized"}')
    with caplog.at_level("DEBUG"), pytest.raises(telegram_send.TelegramNotConfigured) as bad:
        telegram_send.send_message(1, "hi")
    assert str(bad.value) == "" and bad.value.__cause__ is None
    assert [r.levelname for r in caplog.records] == ["ERROR"]
    assert TOKEN not in caplog.text and "api.telegram.org" not in caplog.text


def test_a_429_without_retry_after_still_retries(tg: FakeTelegram) -> None:
    tg.answer = (429, b"not json")
    with pytest.raises(RetryAfter) as limited:
        telegram_send.send_message(1, "hi")
    assert limited.value.seconds == 0


def test_no_token_fails_closed(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_send, "transport", fake)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    get_settings.cache_clear()
    with pytest.raises(telegram_send.TelegramNotConfigured):
        telegram_send.send_message(1, "hi")
    assert fake.sent == []


class _Conn:
    """Stands in for http.client.HTTPSConnection; fails at the chosen step with the token."""

    fail_at = "connect"

    def __init__(self, host: str, timeout: float, context: Any) -> None:
        assert (host, timeout) == ("api.telegram.org", telegram_send.CONNECT_TIMEOUT)
        self.sock = None

    def _maybe(self, step: str) -> None:
        if self.fail_at == step:
            exc = TimeoutError if step == "getresponse" else OSError
            raise exc(f"failed for https://api.telegram.org/bot{TOKEN}/sendMessage")

    def connect(self) -> None:
        self._maybe("connect")

    def request(self, *args: Any, **kwargs: Any) -> None:
        self._maybe("request")

    def getresponse(self):  # type: ignore[no-untyped-def]
        self._maybe("getresponse")
        return type("R", (), {"status": 200, "read": lambda self: b"{}"})()

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("step", "error"),
    [
        ("connect", telegram_send.NotSent),
        ("request", telegram_send.NotSent),
        ("getresponse", telegram_send.NoAnswer),
    ],
)
def test_https_transport_maps_failures_without_leaking_the_token(
    monkeypatch, step: str, error: type
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_Conn, "fail_at", step)
    monkeypatch.setattr(http.client, "HTTPSConnection", _Conn)
    with pytest.raises(error) as caught:
        telegram_send.https_transport(f"/bot{TOKEN}/sendMessage", b"{}")
    assert TOKEN not in str(caught.value) and TOKEN not in repr(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_https_transport_returns_the_answer(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_Conn, "fail_at", "")
    monkeypatch.setattr(http.client, "HTTPSConnection", _Conn)
    assert telegram_send.https_transport("/x", b"{}") == (200, b"{}")


def test_a_failed_send_never_logs_the_token(  # type: ignore[no-untyped-def]
    db: Session, scene, tg: FakeTelegram, caplog
) -> None:
    _, owner, _, request = scene
    owner.email_verified_at = None
    db.flush()
    notifications.fan_out_new_request(db, {"request_id": request.id})
    tg.raises = telegram_send.NotSent()
    with caplog.at_level("DEBUG"), pytest.raises(telegram_send.TelegramUnavailable):
        _send_all(db, owner)
    tg.raises, tg.answer = None, (400, b"{}")
    with caplog.at_level("DEBUG"):
        _send_all(db, owner)
    assert TOKEN not in caplog.text and str(owner.telegram_id) not in caplog.text


def test_every_event_and_notify_has_a_handler() -> None:
    loop.install_handlers()
    for kind in (*EVENTS, NOTIFY):
        assert kind in jobs.HANDLERS
