"""Reference data: cities, districts, categories, specialties and search synonyms.

Run with `python -m app.seed`. Safe to run on every start:
- rows are matched by slug; names, region and order are updated from the files;
- synonyms of seeded specialties are made equal to the files (extra ones are removed);
- cities, districts, categories and specialties are never deleted.

Until the admin panel can edit these tables (week 10) the files are the source of truth (R-018).
Russian names live in the JSON files, Tajik names in `tg_names.csv` for the owner's review.
"""

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Category, City, District, Specialty, SpecialtySynonym

DATA_DIR = Path(__file__).resolve().parent / "data"
SEEDED_SYNONYM_LANGS = ("ru", "tg")


class SeedDataError(ValueError):
    """The seed files are inconsistent; nothing was written."""


@dataclass
class SeedStats:
    cities: int = 0
    districts: int = 0
    categories: int = 0
    specialties: int = 0
    synonyms_added: int = 0
    synonyms_removed: int = 0


@dataclass
class SeedData:
    cities: list[dict] = field(default_factory=list)
    categories: list[dict] = field(default_factory=list)


def _synonyms(raw: str | list[str]) -> list[str]:
    items = raw.split(";") if isinstance(raw, str) else raw
    out: list[str] = []
    for item in items:
        word = " ".join(item.split()).lower()
        if word and word not in out:
            out.append(word)
    return out


def _tg_rows(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["kind"], row["slug"])
            if key in rows:
                raise SeedDataError(f"tg_names.csv: duplicate row {key}")
            if not row["tg"].strip():
                raise SeedDataError(f"tg_names.csv: empty Tajik name for {key}")
            if row["kind"] != "specialty" and row["tg_synonyms"].strip():
                raise SeedDataError(f"tg_names.csv: only specialties have synonyms {key}")
            rows[key] = row
    return rows


def load(data_dir: Path = DATA_DIR) -> SeedData:
    """Read the files, attach Tajik names and check that both languages cover the same rows."""
    places = json.loads((data_dir / "places.json").read_text(encoding="utf-8"))
    catalog = json.loads((data_dir / "categories.json").read_text(encoding="utf-8"))
    tg = _tg_rows(data_dir / "tg_names.csv")
    used: set[tuple[str, str]] = set()

    def attach(kind: str, slug: str, item: dict) -> None:
        key = (kind, slug)
        row = tg.get(key)
        if row is None:
            raise SeedDataError(f"tg_names.csv: no row for {key}")
        if row["ru"] != item["name_ru"]:
            raise SeedDataError(
                f"tg_names.csv: Russian name for {key} is {row['ru']!r}, "
                f"the JSON file says {item['name_ru']!r}"
            )
        item["name_tg"] = row["tg"].strip()
        if kind == "specialty":
            item["synonyms_tg"] = _synonyms(row["tg_synonyms"])
        used.add(key)

    for city in places["cities"]:
        attach("city", city["slug"], city)
        for district in city.get("districts", []):
            attach("district", f"{city['slug']}/{district['slug']}", district)
    for category in catalog["categories"]:
        attach("category", category["slug"], category)
        for specialty in category["specialties"]:
            attach("specialty", specialty["slug"], specialty)
            specialty["synonyms_ru"] = _synonyms(specialty.get("synonyms", []))

    extra = sorted(set(tg) - used)
    if extra:
        raise SeedDataError(f"tg_names.csv: rows without a match in the JSON files: {extra}")
    return SeedData(cities=places["cities"], categories=catalog["categories"])


def _upsert(session: Session, model, rows: list[dict], keys: list[str]) -> dict:
    """Insert or update rows matched by `keys`; return {key tuple: id}."""
    if not rows:
        return {}
    stmt = insert(model).values(rows)
    updates = {c: stmt.excluded[c] for c in rows[0] if c not in keys}
    stmt = stmt.on_conflict_do_update(index_elements=keys, set_=updates).returning(
        model.id, *(getattr(model, k) for k in keys)
    )
    return {tuple(r[1:]): r[0] for r in session.execute(stmt)}


def apply(session: Session, data: SeedData | None = None) -> SeedStats:
    """Write the reference data in the caller's transaction."""
    data = data or load()
    stats = SeedStats()

    city_ids = _upsert(
        session,
        City,
        [
            {
                "slug": c["slug"],
                "name_ru": c["name_ru"],
                "name_tg": c["name_tg"],
                "region": c["region"],
                "kind": c["kind"],
                "sort_order": i,
            }
            for i, c in enumerate(data.cities)
        ],
        ["slug"],
    )
    stats.cities = len(city_ids)
    district_rows = [
        {
            "city_id": city_ids[(c["slug"],)],
            "slug": d["slug"],
            "name_ru": d["name_ru"],
            "name_tg": d["name_tg"],
            "sort_order": i,
        }
        for c in data.cities
        for i, d in enumerate(c.get("districts", []))
    ]
    stats.districts = len(_upsert(session, District, district_rows, ["city_id", "slug"]))

    category_ids = _upsert(
        session,
        Category,
        [
            {"slug": c["slug"], "name_ru": c["name_ru"], "name_tg": c["name_tg"], "sort_order": i}
            for i, c in enumerate(data.categories)
        ],
        ["slug"],
    )
    stats.categories = len(category_ids)
    specialties = [
        s | {"category_slug": c["slug"]} for c in data.categories for s in c["specialties"]
    ]
    specialty_ids = _upsert(
        session,
        Specialty,
        [
            {
                "category_id": category_ids[(s["category_slug"],)],
                "slug": s["slug"],
                "name_ru": s["name_ru"],
                "name_tg": s["name_tg"],
                "sort_order": i,
            }
            for i, s in enumerate(specialties)
        ],
        ["slug"],
    )
    stats.specialties = len(specialty_ids)

    wanted = {
        (specialty_ids[(s["slug"],)], lang, word)
        for s in specialties
        for lang in SEEDED_SYNONYM_LANGS
        for word in s[f"synonyms_{lang}"]
    }
    existing: set[tuple[int, str, str]] = set(
        session.execute(
            select(SpecialtySynonym.specialty_id, SpecialtySynonym.lang, SpecialtySynonym.text)
            .where(SpecialtySynonym.specialty_id.in_(list(specialty_ids.values())))
            .where(SpecialtySynonym.lang.in_(SEEDED_SYNONYM_LANGS))
        )
    )
    stale = existing - wanted
    if stale:
        session.execute(
            delete(SpecialtySynonym).where(
                tuple_(
                    SpecialtySynonym.specialty_id, SpecialtySynonym.lang, SpecialtySynonym.text
                ).in_(list(stale))
            )
        )
    missing = wanted - existing
    if missing:
        session.execute(
            insert(SpecialtySynonym).values(
                [
                    {"specialty_id": sid, "lang": lang, "text": text}
                    for sid, lang, text in sorted(missing)
                ]
            )
        )
    stats.synonyms_added, stats.synonyms_removed = len(missing), len(stale)
    session.flush()
    return stats
