"""The database itself refuses invalid data, whatever the service layer does.

Each test checks the exact constraint name, so a test cannot pass because of an unrelated error.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models import (
    Category,
    City,
    Complaint,
    District,
    ImportBlocklist,
    JobRequest,
    JobResponse,
    MasterArea,
    MasterProfile,
    MasterSpecialty,
    ProfileClaim,
    RequestPhoto,
    Review,
    Specialty,
    SpecialtySynonym,
    SupportThread,
    User,
)

NOW = datetime(2026, 10, 24, tzinfo=UTC)
_seq = count(1)


def reject(db: Session, constraint: str, *objs: Base) -> None:
    """Adding `objs` must fail on `constraint`; the session stays usable afterwards."""
    with pytest.raises(IntegrityError) as exc, db.begin_nested():
        db.add_all(objs)
        db.flush()
    assert exc.value.orig.diag.constraint_name == constraint


def accept(db: Session, *objs: Base) -> None:
    db.add_all(objs)
    db.flush()


def user(**kw) -> User:
    return User(**{"email": f"user{next(_seq)}@example.com", "name": "Test"} | kw)


def master(owner: User | None, **kw) -> MasterProfile:
    return MasterProfile(
        **{"user_id": owner.id if owner else None, "slug": f"master-{next(_seq)}"}
        | {"display_name": "Master"}
        | kw
    )


@pytest.fixture
def places(db: Session) -> dict:
    dushanbe = City(slug="t-dushanbe", name_ru="Д", name_tg="Д", region="dushanbe", kind="city")
    khujand = City(slug="t-khujand", name_ru="Х", name_tg="Х", region="sughd", kind="city")
    accept(db, dushanbe, khujand)
    sino = District(city_id=dushanbe.id, slug="sino", name_ru="С", name_tg="С")
    accept(db, sino)
    return {"dushanbe": dushanbe, "khujand": khujand, "sino": sino}


@pytest.fixture
def specialty(db: Session) -> Specialty:
    category = Category(slug="t-cat", name_ru="К", name_tg="К")
    accept(db, category)
    spec = Specialty(category_id=category.id, slug="t-spec", name_ru="С", name_tg="С")
    accept(db, spec)
    return spec


@pytest.fixture
def client_user(db: Session) -> User:
    u = user()
    accept(db, u)
    return u


@pytest.fixture
def a_master(db: Session) -> MasterProfile:
    owner = user()
    accept(db, owner)
    m = master(owner)
    accept(db, m)
    return m


@pytest.fixture
def a_request(db: Session, client_user: User, places: dict) -> JobRequest:
    r = JobRequest(
        client_id=client_user.id,
        city_id=places["dushanbe"].id,
        district_id=places["sino"].id,
        description="Fix a leaking pipe",
        timing="urgent",
    )
    accept(db, r)
    return r


# ---- users ----


def test_user_email_is_stored_lowercase(db: Session) -> None:
    reject(db, "ck_users_email_lowercase", user(email="Someone@Example.com"))


def test_user_email_is_unique(db: Session) -> None:
    accept(db, user(email="same@example.com"))
    reject(db, "uq_users_email", user(email="same@example.com"))


def test_user_phone_must_be_e164(db: Session) -> None:
    reject(db, "ck_users_phone_format", user(phone="901234567"))


def test_verified_phone_belongs_to_one_account(db: Session) -> None:
    accept(db, user(phone="+992901234567"), user(phone="+992901234567"))  # unverified: fine
    accept(db, user(phone="+992901112233", phone_verified_at=NOW))
    reject(db, "uq_users_verified_phone", user(phone="+992901112233", phone_verified_at=NOW))


def test_user_locale_is_ru_or_tg(db: Session) -> None:
    reject(db, "ck_users_locale", user(locale="en"))


# ---- master profiles ----


@pytest.mark.parametrize("slug", ["ab", "Has-Caps", "with space", "under_score", "кириллица"])
def test_master_slug_format(db: Session, client_user: User, slug: str) -> None:
    reject(db, "ck_master_profiles_slug_format", master(client_user, slug=slug))


def test_master_slug_is_unique(db: Session) -> None:
    a, b = user(), user()
    accept(db, a, b)
    accept(db, master(a, slug="usto-ali"))
    reject(db, "uq_master_profiles_slug", master(b, slug="usto-ali"))


@pytest.mark.parametrize("years", [-1, 61])
def test_master_experience_range(db: Session, client_user: User, years: int) -> None:
    reject(db, "ck_master_profiles_experience_years", master(client_user, experience_years=years))


def test_one_master_profile_per_user(db: Session, a_master: MasterProfile) -> None:
    reject(db, "uq_master_profiles_user_id", master(db.get(User, a_master.user_id)))


def test_plan_is_free_in_v1(db: Session, client_user: User) -> None:
    reject(db, "ck_master_profiles_plan", master(client_user, plan="pro"))


def test_self_created_profile_needs_a_user(db: Session) -> None:
    reject(db, "ck_master_profiles_self_has_user", master(None))


def test_admin_added_profile_needs_consent(db: Session) -> None:
    reject(db, "ck_master_profiles_admin_has_consent", master(None, origin="admin"))
    accept(db, master(None, origin="admin", consent_at=NOW))


def test_imported_profile_records_its_source(db: Session) -> None:
    reject(db, "ck_master_profiles_import_has_source", master(None, origin="import"))
    accept(
        db,
        master(
            None,
            origin="import",
            source_url="https://example.tj/masters/1",
            imported_at=NOW,
            source_phone="+992901234567",
        ),
    )


def test_claimed_profile_has_an_owner(db: Session) -> None:
    reject(
        db,
        "ck_master_profiles_claimed_has_user",
        master(
            None,
            origin="import",
            source_url="https://example.tj/masters/2",
            imported_at=NOW,
            claimed_at=NOW,
        ),
    )


def test_call_counter_cannot_go_negative(db: Session, client_user: User) -> None:
    reject(db, "ck_master_profiles_counters_not_negative", master(client_user, call_count=-1))


# ---- work area and specialties ----


def test_area_district_must_belong_to_the_city(
    db: Session, a_master: MasterProfile, places: dict
) -> None:
    reject(
        db,
        "fk_master_areas_district_in_city",
        MasterArea(
            master_id=a_master.id, city_id=places["khujand"].id, district_id=places["sino"].id
        ),
    )


def test_area_rows_are_unique(db: Session, a_master: MasterProfile, places: dict) -> None:
    city, sino = places["dushanbe"].id, places["sino"].id
    accept(
        db,
        MasterArea(master_id=a_master.id, city_id=city),
        MasterArea(master_id=a_master.id, city_id=city, district_id=sino),
    )
    reject(db, "uq_master_areas_whole_city", MasterArea(master_id=a_master.id, city_id=city))
    reject(
        db,
        "uq_master_areas_district",
        MasterArea(master_id=a_master.id, city_id=city, district_id=sino),
    )


def test_specialty_in_use_cannot_be_deleted(
    db: Session, a_master: MasterProfile, specialty: Specialty
) -> None:
    accept(db, MasterSpecialty(master_id=a_master.id, specialty_id=specialty.id))
    with pytest.raises(IntegrityError) as exc, db.begin_nested():
        db.execute(delete(Specialty).where(Specialty.id == specialty.id))
    assert exc.value.orig.diag.constraint_name == "fk_master_specialties_specialty_id_specialties"


def test_synonyms_are_lowercase_and_unique(db: Session, specialty: Specialty) -> None:
    reject(
        db,
        "ck_specialty_synonyms_text_lowercase",
        SpecialtySynonym(specialty_id=specialty.id, lang="ru", text="Засор"),
    )
    accept(db, SpecialtySynonym(specialty_id=specialty.id, lang="ru", text="засор"))
    reject(
        db,
        "uq_specialty_synonyms_specialty_id_lang_text",
        SpecialtySynonym(specialty_id=specialty.id, lang="ru", text="засор"),
    )


# ---- requests and responses ----


def test_request_district_must_belong_to_the_city(
    db: Session, client_user: User, places: dict
) -> None:
    reject(
        db,
        "fk_requests_district_in_city",
        JobRequest(
            client_id=client_user.id,
            city_id=places["khujand"].id,
            district_id=places["sino"].id,
            description="x",
            timing="urgent",
        ),
    )


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"budget_amount": Decimal(500)}, "ck_requests_budget_amount_with_unit"),
        ({"budget_unit": "m2"}, "ck_requests_budget_amount_with_unit"),
        ({"budget_amount": Decimal(0), "budget_unit": "fixed"}, "ck_requests_budget_positive"),
        ({"budget_amount": Decimal(5), "budget_unit": "kg"}, "ck_requests_budget_unit"),
        ({"timing": "date"}, "ck_requests_timing_date"),
        ({"timing_date": date(2026, 11, 1)}, "ck_requests_timing_date"),
        ({"description": "   "}, "ck_requests_description_not_blank"),
        ({"status": "master_chosen"}, "ck_requests_chosen_master_set"),
        ({"status": "completed"}, "ck_requests_chosen_master_set"),
        ({"status": "closed"}, "ck_requests_closed_at"),
        ({"status": "archived"}, "ck_requests_status"),
    ],
)
def test_request_field_rules(
    db: Session, client_user: User, places: dict, fields: dict, constraint: str
) -> None:
    base = {
        "client_id": client_user.id,
        "city_id": places["dushanbe"].id,
        "description": "Tile the bathroom",
        "timing": "urgent",
    }
    reject(db, constraint, JobRequest(**base | fields))


def test_completed_request_has_master_and_date(
    db: Session, a_request: JobRequest, a_master: MasterProfile
) -> None:
    a_request.status = "completed"
    a_request.chosen_master_id = a_master.id
    reject(db, "ck_requests_completed_at", a_request)


def test_request_has_at_most_four_photos(db: Session, a_request: JobRequest) -> None:
    def photo(position: int) -> RequestPhoto:
        n = next(_seq)
        return RequestPhoto(
            request_id=a_request.id,
            storage_key=f"r/{n}.webp",
            thumb_key=f"r/{n}.thumb.webp",
            width=1600,
            height=1200,
            position=position,
        )

    accept(db, *(photo(p) for p in range(4)))
    reject(db, "ck_request_photos_position", photo(4))
    reject(db, "uq_request_photos_request_id_position", photo(0))


def test_one_response_per_master_per_request(
    db: Session, a_request: JobRequest, a_master: MasterProfile
) -> None:
    accept(db, JobResponse(request_id=a_request.id, master_id=a_master.id))
    reject(
        db,
        "uq_responses_request_id_master_id",
        JobResponse(request_id=a_request.id, master_id=a_master.id, message="again"),
    )


def test_response_price_needs_a_unit(
    db: Session, a_request: JobRequest, a_master: MasterProfile
) -> None:
    reject(
        db,
        "ck_responses_price_amount_with_unit",
        JobResponse(request_id=a_request.id, master_id=a_master.id, price_amount=Decimal(300)),
    )


# ---- reviews ----


def _review(req: JobRequest, m: MasterProfile, **kw) -> Review:
    return Review(
        **{"request_id": req.id, "master_id": m.id, "author_id": req.client_id}
        | {"rating": 5, "text": "Good job"}
        | kw
    )


@pytest.mark.parametrize("rating", [0, 6])
def test_review_rating_is_1_to_5(
    db: Session, a_request: JobRequest, a_master: MasterProfile, rating: int
) -> None:
    reject(db, "ck_reviews_rating_range", _review(a_request, a_master, rating=rating))


def test_one_review_per_request(
    db: Session, a_request: JobRequest, a_master: MasterProfile
) -> None:
    accept(db, _review(a_request, a_master))
    reject(db, "uq_reviews_request_id", _review(a_request, a_master, rating=1))


def test_master_reply_has_a_time(
    db: Session, a_request: JobRequest, a_master: MasterProfile
) -> None:
    reject(db, "ck_reviews_reply_with_time", _review(a_request, a_master, master_reply="Thanks"))


# ---- complaints ----


def test_complaint_has_exactly_one_target(
    db: Session, client_user: User, a_request: JobRequest, a_master: MasterProfile
) -> None:
    reject(db, "ck_complaints_one_target", Complaint(reporter_id=client_user.id, reason="spam"))
    reject(
        db,
        "ck_complaints_one_target",
        Complaint(
            reporter_id=client_user.id,
            reason="spam",
            request_id=a_request.id,
            master_profile_id=a_master.id,
        ),
    )
    accept(db, Complaint(reporter_id=client_user.id, reason="spam", request_id=a_request.id))


def test_complaint_reason_other_needs_text(
    db: Session, client_user: User, a_master: MasterProfile
) -> None:
    def complaint(**kw) -> Complaint:
        return Complaint(reporter_id=client_user.id, master_profile_id=a_master.id, **kw)

    reject(db, "ck_complaints_other_needs_text", complaint(reason="other"))
    reject(db, "ck_complaints_other_needs_text", complaint(reason="other", text="  "))
    reject(db, "ck_complaints_reason", complaint(reason="boring"))
    accept(
        db, complaint(reason="other", text="Asks for prepayment"), complaint(reason="wrong_number")
    )


def test_resolved_complaint_has_a_time(
    db: Session, client_user: User, a_request: JobRequest
) -> None:
    reject(
        db,
        "ck_complaints_resolved_at",
        Complaint(
            reporter_id=client_user.id, request_id=a_request.id, reason="spam", status="resolved"
        ),
    )


# ---- support ----


def test_support_thread_is_for_a_user_or_a_guest(db: Session, client_user: User) -> None:
    reject(db, "ck_support_threads_user_or_guest", SupportThread())
    reject(
        db,
        "ck_support_threads_user_or_guest",
        SupportThread(user_id=client_user.id, guest_email="guest@example.com"),
    )
    accept(
        db, SupportThread(user_id=client_user.id), SupportThread(guest_email="guest@example.com")
    )


def test_closed_thread_has_a_time(db: Session, client_user: User) -> None:
    reject(
        db, "ck_support_threads_closed_at", SupportThread(user_id=client_user.id, status="closed")
    )


# ---- imported profiles ----


def test_one_pending_claim_per_user_and_profile(db: Session, client_user: User) -> None:
    profile = master(None, origin="import", source_url="https://example.tj/m/9", imported_at=NOW)
    accept(db, profile)

    def claim(**kw) -> ProfileClaim:
        return ProfileClaim(
            master_id=profile.id, user_id=client_user.id, phone="+992900000001", **kw
        )

    accept(db, claim(status="rejected", resolved_at=NOW), claim())
    reject(db, "uq_profile_claims_pending", claim())


def test_blocklist_keeps_only_hashes(db: Session) -> None:
    reject(
        db,
        "ck_import_blocklist_value_sha256_hex",
        ImportBlocklist(kind="phone", value_sha256="+992901234567"),
    )
    accept(db, ImportBlocklist(kind="phone", value_sha256="a" * 64))
    reject(
        db,
        "uq_import_blocklist_kind_value_sha256",
        ImportBlocklist(kind="phone", value_sha256="a" * 64),
    )
