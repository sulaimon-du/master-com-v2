import re
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, City, District, Specialty, SpecialtySynonym
from app.models.common import REF_SLUG_RE
from app.seed import DATA_DIR, SeedDataError, apply, load


def _count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_seed_files_cover_the_brief() -> None:
    data = load()
    assert len(data.categories) == 12  # BRIEF §5
    specialties = [s for c in data.categories for s in c["specialties"]]
    slugs = [s["slug"] for s in specialties]
    assert len(slugs) == len(set(slugs))
    for s in specialties:
        assert re.match(REF_SLUG_RE, s["slug"]), s["slug"]
        assert s["name_tg"].strip(), s["slug"]
        assert s["synonyms_ru"], f"{s['slug']} has no Russian synonyms"
        assert all(w == w.lower() for w in s["synonyms_ru"] + s["synonyms_tg"])
    for c in data.categories:
        assert c["specialties"], c["slug"]

    city_slugs = [c["slug"] for c in data.cities]
    assert len(city_slugs) == len(set(city_slugs))
    dushanbe = next(c for c in data.cities if c["slug"] == "dushanbe")
    assert {d["slug"] for d in dushanbe["districts"]} == {
        "ismoili-somoni",
        "sino",
        "firdavsi",
        "shohmansur",
    }
    assert {c["region"] for c in data.cities} == {"dushanbe", "rrs", "sughd", "khatlon", "gbao"}


def test_seed_writes_reference_data(db: Session) -> None:
    data = load()
    stats = apply(db, data)

    assert _count(db, Category) == stats.categories == 12
    assert _count(db, Specialty) == stats.specialties
    assert _count(db, City) == stats.cities == len(data.cities)
    assert _count(db, District) == stats.districts == 4
    assert _count(db, SpecialtySynonym) == stats.synonyms_added

    plumber = db.scalars(select(Specialty).where(Specialty.slug == "santekhnik")).one()
    assert plumber.category.slug == "santekhnika"
    assert plumber.name_tg == "Сантехник"
    words = {(s.lang, s.text) for s in plumber.synonyms}
    assert ("ru", "смеситель") in words
    assert ("tg", "қубур") in words

    sino = db.scalars(select(District).where(District.slug == "sino")).one()
    assert sino.city.slug == "dushanbe"
    assert sino.name_tg == "Сино"


def test_seed_is_idempotent(db: Session) -> None:
    first = apply(db)
    second = apply(db)
    assert second.synonyms_added == second.synonyms_removed == 0
    assert (second.cities, second.specialties) == (first.cities, first.specialties)
    assert _count(db, SpecialtySynonym) == first.synonyms_added


def test_seed_restores_names_and_syncs_synonyms(db: Session) -> None:
    apply(db)
    plumber = db.scalars(select(Specialty).where(Specialty.slug == "santekhnik")).one()
    plumber.name_tg = "typo"
    db.add_all(
        [
            SpecialtySynonym(specialty_id=plumber.id, lang="ru", text="устаревшее слово"),
            SpecialtySynonym(specialty_id=plumber.id, lang="lat", text="santehnik"),
        ]
    )
    db.flush()

    stats = apply(db)
    db.expire_all()

    assert stats.synonyms_removed == 1
    assert plumber.name_tg == "Сантехник"
    texts = {s.text for s in plumber.synonyms}
    assert "устаревшее слово" not in texts
    # Hand-added Latin spellings are not managed by the seed and stay.
    assert "santehnik" in texts


def _copy_data(tmp_path: Path) -> Path:
    shutil.copytree(DATA_DIR, tmp_path / "data")
    return tmp_path / "data"


def test_load_rejects_russian_name_drift(tmp_path: Path) -> None:
    data_dir = _copy_data(tmp_path)
    csv_path = data_dir / "tg_names.csv"
    csv_path.write_text(
        csv_path.read_text(encoding="utf-8").replace(
            "specialty,santekhnik,Сантехник,", "specialty,santekhnik,Водопроводчик,"
        ),
        encoding="utf-8",
    )
    with pytest.raises(SeedDataError, match="santekhnik"):
        load(data_dir)


def test_load_rejects_missing_tajik_row(tmp_path: Path) -> None:
    data_dir = _copy_data(tmp_path)
    csv_path = data_dir / "tg_names.csv"
    lines = csv_path.read_text(encoding="utf-8").splitlines(keepends=True)
    csv_path.write_text(
        "".join(line for line in lines if not line.startswith("city,khujand,")),
        encoding="utf-8",
    )
    with pytest.raises(SeedDataError, match="khujand"):
        load(data_dir)


def test_load_rejects_unknown_tajik_row(tmp_path: Path) -> None:
    data_dir = _copy_data(tmp_path)
    csv_path = data_dir / "tg_names.csv"
    with csv_path.open("a", encoding="utf-8") as f:
        f.write("city,atlantis,Атлантида,Атлантида,,\n")
    with pytest.raises(SeedDataError, match="atlantis"):
        load(data_dir)
