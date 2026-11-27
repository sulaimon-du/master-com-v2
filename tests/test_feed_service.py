from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import JobResponse, MasterProfile
from app.services import feed as feed_svc
from app.services.search_index import refresh_search
from tests import feed_support as fs


@pytest.fixture(autouse=True)
def only_own_requests(db: Session) -> None:
    fs.hide_other_requests(db)


def _ids(db: Session, user, cursor: str | None = None) -> list[int]:  # type: ignore[no-untyped-def]
    return [c.id for c in feed_svc.feed(db, user, cursor).items]


def _dushanbe(db: Session) -> tuple[int, int, int]:
    """Dushanbe's id and two of its district ids."""
    return (
        fs.city(db, "dushanbe").id,
        fs.district(db, "dushanbe", "sino").id,
        fs.district(db, "dushanbe", "firdavsi").id,
    )


# --- matching matrix (spec D3) ----------------------------------------------------------------


def test_district_master_sees_only_requests_in_their_districts(db: Session) -> None:
    dushanbe, sino, firdavsi = _dushanbe(db)
    owner, _ = fs.master(db, districts=(("dushanbe", "sino"),))
    in_sino = fs.post(db, city_id=dushanbe, district_id=sino)
    fs.post(db, city_id=dushanbe, district_id=firdavsi)
    assert _ids(db, owner) == [in_sino.id]


def test_whole_city_master_sees_every_district(db: Session) -> None:
    dushanbe, sino, firdavsi = _dushanbe(db)
    owner, _ = fs.master(db, cities=("dushanbe",))
    a = fs.post(db, city_id=dushanbe, district_id=sino)
    b = fs.post(db, city_id=dushanbe, district_id=firdavsi)
    assert _ids(db, owner) == [b.id, a.id]


def test_city_without_districts_matches_by_city(db: Session) -> None:
    town = fs.city_without_districts(db)
    dushanbe, sino, _ = _dushanbe(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    here = fs.post(db, city_id=town.id)
    fs.post(db, city_id=dushanbe, district_id=sino)  # another city
    assert _ids(db, owner) == [here.id]


def test_specialty_must_overlap(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, specs=("santekhnik",), city_ids=(town.id,))
    fs.post(db, specs=("arkhitektor",), city_id=town.id)
    both = fs.post(db, specs=("arkhitektor", "santekhnik"), city_id=town.id)
    assert _ids(db, owner) == [both.id]


def test_own_request_is_not_in_the_feed(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    fs.post(db, owner, city_id=town.id)
    assert _ids(db, owner) == []


def test_already_responded_request_leaves_the_feed_until_withdrawn(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, profile = fs.master(db, city_ids=(town.id,))
    request = fs.post(db, city_id=town.id)
    response = JobResponse(request_id=request.id, master_id=profile.id)
    db.add(response)
    db.flush()
    assert _ids(db, owner) == []
    response.withdrawn_at = utcnow()
    db.flush()
    assert _ids(db, owner) == [request.id]


def test_paused_hidden_and_not_open_requests_are_not_in_the_feed(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    paused = fs.post(db, city_id=town.id)
    paused.feed_paused_at = utcnow()
    hidden = fs.post(db, city_id=town.id)
    hidden.hidden_at = utcnow()
    closed = fs.post(db, city_id=town.id)
    closed.status, closed.close_reason, closed.closed_at = "closed", "client_closed", utcnow()
    visible = fs.post(db, city_id=town.id)
    db.flush()
    assert _ids(db, owner) == [visible.id]


def test_requests_of_a_blocked_or_deleted_client_are_not_in_the_feed(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    blocked, deleted = fs.user(db), fs.user(db)
    fs.post(db, blocked, city_id=town.id)
    fs.post(db, deleted, city_id=town.id)
    blocked.blocked_at = utcnow()
    deleted.deleted_at = utcnow()
    db.flush()
    assert _ids(db, owner) == []


def test_card_never_carries_the_address(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    fs.post(db, city_id=town.id, address="ул. Рудаки, 1, кв. 5")
    card = feed_svc.feed(db, owner, None).items[0]
    assert "address" not in card.model_dump()
    assert "Рудаки" not in card.model_dump_json()


# --- who gets a feed at all -------------------------------------------------------------------


def test_unpublished_master_sees_nothing(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, ready=False)
    fs.post(db, city_id=town.id)
    page = feed_svc.feed(db, owner, None)
    assert page.eligible is False
    assert page.items == []


def test_plain_user_sees_nothing(db: Session) -> None:
    town = fs.city_without_districts(db)
    fs.post(db, city_id=town.id)
    page = feed_svc.feed(db, fs.user(db), None)
    assert page.eligible is False
    assert page.items == []


def test_hidden_master_sees_nothing(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, profile = fs.master(db, city_ids=(town.id,))
    fs.post(db, city_id=town.id)
    profile.hidden_at = utcnow()
    db.flush()
    refresh_search(db, profile)
    assert feed_svc.feed(db, owner, None).eligible is False


def test_blocked_master_sees_nothing_even_with_a_stale_index(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    fs.post(db, city_id=town.id)
    owner.blocked_at = utcnow()  # written straight to the row; master_search not refreshed
    db.flush()
    page = feed_svc.feed(db, owner, None)
    assert page.eligible is False
    assert page.items == []


def test_unpublished_profile_with_a_stale_index_sees_nothing(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, profile = fs.master(db, city_ids=(town.id,))
    fs.post(db, city_id=town.id)
    db.execute(
        update(MasterProfile).where(MasterProfile.id == profile.id).values(published_at=None)
    )
    db.expire_all()
    assert feed_svc.feed(db, owner, None).eligible is False


# --- ordering and cursor pagination -----------------------------------------------------------


def test_cursor_pagination_is_stable_across_new_requests(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    base = utcnow() - timedelta(days=1)
    made = [fs.post(db, city_id=town.id) for _ in range(feed_svc.PAGE_SIZE + 5)]
    # Two requests share a timestamp so the id breaks the tie.
    for i, request in enumerate(made):
        request.created_at = base + timedelta(minutes=i // 2)
    db.flush()
    expected = [r.id for r in sorted(made, key=lambda r: (r.created_at, r.id), reverse=True)]

    first = feed_svc.feed(db, owner, None)
    assert [c.id for c in first.items] == expected[: feed_svc.PAGE_SIZE]
    assert first.next_cursor is not None
    fs.post(db, city_id=town.id)  # a newer request between page loads does not shift page 2
    second = feed_svc.feed(db, owner, first.next_cursor)
    assert [c.id for c in second.items] == expected[feed_svc.PAGE_SIZE :]
    assert second.next_cursor is None


def test_garbage_cursor_starts_from_the_top(db: Session) -> None:
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    request = fs.post(db, city_id=town.id)
    for cursor in ("", "x", "1-2-3", "-1-5", "9" * 40 + "-1", "1-\x00", "1.5-2"):
        assert _ids(db, owner, cursor) == [request.id], cursor


def test_cursor_round_trip() -> None:
    at = utcnow()
    assert feed_svc.parse_cursor(feed_svc.make_cursor(at, 42)) == (at, 42)


def test_matching_request_uses_the_feed_rules(db: Session) -> None:
    """`matching_request` (for respond and the master's request page) shares the feed's match."""
    town = fs.city_without_districts(db)
    owner, _ = fs.master(db, city_ids=(town.id,))
    shown = fs.post(db, city_id=town.id)
    other = fs.post(db, specs=("arkhitektor",), city_id=town.id)
    eligible = feed_svc.eligible_master(db, owner)
    assert eligible is not None
    assert feed_svc.matching_request(db, eligible, shown.id) is not None
    assert feed_svc.matching_request(db, eligible, other.id) is None
    assert feed_svc.matching_request(db, eligible, 10**9) is None
