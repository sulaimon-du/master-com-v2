# Search, SEO pages and sitemap: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clients find visible masters through `/search` (typo- and transliteration-tolerant, synonyms, highlighting) and through indexable ru/tg landing pages, with a sitemap, robots rules and meta tags; staging is never indexed.

**Architecture:** A denormalized `master_search` row per master (transliterated text + filter arrays) maintained by `refresh_search` inside `refresh_publication`; a sync search service queries it with pg_trgm; routers in `app/web/search.py` and `app/web/seo.py`; SEO helpers (meta, canonical, hreflang, robots) in `app/services/seo.py`.

**Tech Stack:** FastAPI (sync), SQLAlchemy 2.0, Postgres 16 + `pg_trgm`, Jinja2 + HTMX, no new Python dependencies.

**Spec:** `docs/superpowers/specs/2026-11-24-search-seo-sitemap-design.md`

## Global Constraints

- Visible = published, not hidden, owner active (`blocked_at IS NULL AND deleted_at IS NULL`), owner phone verified. Search queries also join `users` and re-check active (defence in depth).
- No contacts on any list/SEO page. No `|safe`; highlighting via segments + autoescape.
- Free text: trimmed, ≤ 100 chars, lowercased, transliterated with `masters.slugify`'s table (Russian + ғ→gh, ӣ→i, қ→q, ӯ→u, ҳ→h, ҷ→j), ≤ 6 words of ≥ 2 chars. Word match: `word_similarity >= 0.4` (tunable, record final in R-025). `statement_timeout` 2 s (SET LOCAL) on search queries → friendly message on timeout.
- URL params: `s` specialty slug, `cat` category slug, `c` city slug, `d` district slug (ignored without `c`), `q`, `available=1`, `sort=relevance|rating|newest` (default relevance with `q`, else rating), `page` 1–50; 20 per page. Unknown values ignored with a notice, never 500.
- SEO URLs: ru `/<city>/<specialty>`, `/<city>/<district>/<specialty>`; tg `/tg/…` same shapes; path language overrides the cookie on these pages. Indexable only with ≥ 3 visible masters (district pages: ≥ 3 masters who named that district); 0 masters → 200 noindex + "post a request"; page 2+ noindex with self canonical incl. `?page=N`.
- `/search` → `noindex, follow`. Non-production: every response `X-Robots-Tag: noindex, nofollow`, robots.txt disallows all. Production robots.txt per spec D5 + Sitemap line.
- Sitemap: indexable SEO pages (ru/tg with `xhtml:link` alternates), visible profiles, public collections; 10-minute in-process cache; ≤ 50 000 URLs.
- All texts via ru/tg locales; regenerate `docs/TRANSLATIONS.md` (`uv run python -m app.core.translations_table`) when keys change.
- Checks before every commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -o addopts=""` (+ `uv run alembic check` when models change).
- Two PRs: **PR A = Tasks 1–4** (branch `week-7-search`), **PR B = Tasks 5–7** (branch `week-7-seo` from main after PR A).

## Review Focus

1. **A master blocked by SQL (no refresh) or a draft** → never in search, SEO pages or sitemap. Test in Task 3 (search) and Task 7 (sitemap).
2. **Hostile free text** (`%`, `_`, `'`, regex chars, 10 000 chars, emoji, only spaces) → no 500, no unbounded scan, sensible empty result. Test in Task 3.
3. **A city or district slug equal to a reserved first segment** (`m`, `master`, `search`, `tg`, …) → impossible; test asserts no collision over the seeded places. Test in Task 5.
4. **Mixed scripts**: query "santehnik" finds "Сантехник", "қубуркаш" finds the tg name/synonym, "Курган-Тюбе" finds Бохтар. Test in Task 3.
5. **Staging indexed by accident** → X-Robots-Tag on every response including 404 and /media. Test in Task 6.

---

### Task 1: Migration — pg_trgm, `master_search`, `place_synonyms`

**Files:**
- Create: `app/models/search.py` (`MasterSearch`, `PlaceSynonym`), migration `migrations/versions/<ts>_0006_search.py`
- Modify: `app/models/__init__.py`, `app/seed/__init__.py` + `app/seed/data/places.json` (synonyms lists), `docs/DATA_MODEL.md`
- Test: `tests/test_constraints.py`, `tests/test_seed.py`

**Interfaces:**
- Produces: `MasterSearch(master_id PK FK master_profiles CASCADE, document text, names text, is_visible bool, is_available bool, has_photos bool, rating_score numeric(4,3) null, published_at timestamptz null, specialty_ids int[], primary_specialty_ids int[], category_ids int[], city_ids int[], district_ids int[], whole_city_ids int[], named_district_ids int[], updated_at)`; GIN trigram indexes on `document` and `names` (`gin_trgm_ops`), GIN on each int array, partial btree on `(rating_score)` `WHERE is_visible`.
- Produces: `PlaceSynonym(id, city_id int null FK, district_id int null FK, lang varchar(3), text varchar(120))`, CHECK exactly one of city/district, unique (coalesce key, lang, lower(text)) as in `specialty_synonyms`.
- Migration enables `CREATE EXTENSION IF NOT EXISTS pg_trgm`; downgrade drops tables (not the extension).
- Seed: `places.json` cities/districts get optional `"synonyms": [...]` (ru) and `"synonyms_tg"`; seed syncs them like specialty synonyms. Claude drafts: Курган-Тюбе/Курган-Тепа→Бохтар, Ленинабад→Худжанд, Чкаловск→Бустон, Кайраккум→Гулистон, Орджоникидзеабад/Кофарнихон→Вахдат, Колхозобод→Абдурахмони Джоми (only for seeded places; skip any not in places.json), plus Latin spellings for the 10 biggest cities (Dushanbe, Khujand, Bokhtar, Kulob, …).

- [ ] **Step 1: Failing tests**: `test_master_search_row_cascades_with_profile`; `test_place_synonym_needs_exactly_one_place`; `test_seed_loads_place_synonyms_idempotently` (two runs, same count); `test_pg_trgm_available` (`SELECT similarity('a','a')`).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**; `alembic revision --autogenerate`, hand-edit (extension, GIN ops classes, partial index), downgrade.
- [ ] **Step 4: Run** `uv run alembic upgrade head && uv run alembic check && uv run alembic downgrade -1 && uv run alembic upgrade head`, tests PASS, full checks.
- [ ] **Step 5: Commit** `feat(search): search index tables, pg_trgm and place synonyms`

### Task 2: `refresh_search` and the rebuild command

**Files:**
- Create: `app/services/search_index.py`
- Modify: `app/services/masters.py` (`refresh_publication` calls `refresh_search` at the end), `app/services/portfolio.py` (after add/delete/undo/hide photo or collection call `refresh_search` — has_photos), `app/cli.py` (`search rebuild`), `app/services/auth.py` `block_user` (refresh the user's master profile if any)
- Test: `tests/test_search_index.py`

**Interfaces:**
- Produces: `translit(text: str) -> str` (lowercase, Latin, same table as slugify but keeps spaces; extract the shared table from masters.py into `app/services/translit.py` and reuse in both); `refresh_search(db, master: MasterProfile) -> None` (upsert row); `rebuild_all(db) -> int`.
- `document` = translit of: display name, headline, bio, each specialty name_ru + name_tg + all its synonyms, its category names, each area's city/district names ru/tg + place synonyms; `names` = translit(display name + " " + headline).

- [ ] **Step 1: Failing tests**: row created on publish and `is_visible` true; unpublish (remove last area) → `is_visible` false; block owner via `block_user` → false; draft → false; `document` contains "santekhnik" for a Сантехник master and the tg name translit; `has_photos` flips after the first public photo and back after deleting it; `rebuild_all` returns the count and fixes a row edited by SQL.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, PASS** + full checks (slugify tests must still pass after the translit extraction).
- [ ] **Step 5: Commit** `feat(search): keep the search index current`

### Task 3: Search service

**Files:**
- Create: `app/services/search.py`, `app/schemas/search.py`
- Test: `tests/test_search_service.py`

**Interfaces:**
- Produces: `@dataclass SearchQuery(q: str | None, specialty: str | None, category: str | None, city: str | None, district: str | None, available: bool, sort: Literal["relevance","rating","newest"] | None, page: int)`; `parse_query(params: Mapping[str, str]) -> tuple[SearchQuery, list[str]]` (notices for ignored values); `search(db, query: SearchQuery, *, lang: str) -> SearchResult` with `SearchResult(items: list[MasterCard], total: int, page: int, pages: int, suggestion: Suggestion | None, specialty_hint: SpecialtyHint | None, timed_out: bool)`; `MasterCard(slug, display_name: list[Segment], headline: list[Segment], avatar_url, specialties: list[list[Segment]] (primary first, ≤ 3), area: str, rating: float | None, photos: list[str] (≤ 4 thumb URLs), is_available)`; `Segment = tuple[str, bool]`; `highlight(text: str, words: list[str]) -> list[Segment]`; `count_visible(db, *, specialty_id, city_id, district_id=None, named_district_only=False) -> int`; `counts_by_city_specialty(db) -> dict[tuple[int, int], int]` and `counts_by_district_specialty(db, *, named_only: bool) -> dict[tuple[int, int], int]` (for SEO links/sitemap).
- [ ] **Step 1: Failing tests** (real DB, seeded catalog, masters created through the masters service): filters each alone and combined; district matches district or whole-city masters; `d` without `c` ignored; category filter; available filter; sort orders and tie-breaks; pagination bounds; `test_transliteration_and_tajik_letters` (Review Focus 4: "santehnik", "қубуркаш", "Курган-Тюбе"); typo ("сантехнк") finds Сантехник; synonym ("протечка" if a synonym) finds the specialty; specialty hint for q resolving to a specialty; "did you mean" when 0 results; `test_hostile_free_text` (Review Focus 2); `test_blocked_by_sql_and_draft_never_listed` (Review Focus 1: set `users.blocked_at` by SQL without refresh → excluded); highlight segments correct and HTML-free; photo strips loaded in one query (assert statement count with an SQLAlchemy event listener ≤ 4 queries per search).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** with SQLAlchemy Core: `SET LOCAL pg_trgm.word_similarity_threshold`, `SET LOCAL statement_timeout = '2s'`, `<%` operator via `.op('<%')`, relevance via `word_similarity`; catch `QueryCanceled`/`OperationalError` → `timed_out=True`.
- [ ] **Step 4: Run, PASS** + full checks.
- [ ] **Step 5: Commit** `feat(search): search service with typo tolerance, transliteration and highlighting`

### Task 4: `/search` page and home page

**Files:**
- Create: `app/web/search.py`, `app/templates/pages/search.html`, `app/templates/partials/master_card.html`
- Modify: `app/main.py`, `app/templates/pages/home.html`, `app/web/pages.py`, locales, `docs/TRANSLATIONS.md`, `docs/DECISIONS.md` (R-025 part A)
- Test: `tests/test_search_web.py`

**Interfaces:**
- Consumes Task 3. Produces route `GET /search` (`search_page`), `<meta name="robots" content="noindex, follow">`.
- [ ] **Step 1: Failing tests**: filters round-trip in the URL; result cards show no contacts (assert phone/telegram strings absent even for a signed-in viewer); highlighted `<mark>` around matches and no raw HTML from names (`<b>` in a display name stays escaped); notices for unknown slugs; timeout message; page links keep filters; home page has the search form and category tiles linking to `/search?cat=…`; `/search` robots meta noindex.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** (pills for active filters, `<select>` for specialty grouped by category, city select; HTMX optional — plain GET form is enough).
- [ ] **Step 4: Run, PASS** + full checks.
- [ ] **Step 5: Commit** `feat(search): search page and home search box`

### Task 5: SEO landing pages (PR B)

**Files:**
- Create: `app/web/seo.py`, `app/services/seo.py`, `app/templates/pages/seo_landing.html`
- Modify: `app/main.py` (register last), `app/web/deps.py` (path language override helper), `app/templates/base.html` (blocks for meta/canonical/hreflang/OG), locales
- Test: `tests/test_seo_pages.py`

**Interfaces:**
- Produces routes `GET /{city}/{specialty}`, `GET /{city}/{district}/{specialty}`, `GET /tg/{city}/{specialty}`, `GET /tg/{city}/{district}/{specialty}` (unknown or inactive slugs → 404); `seo.PageMeta(title, description, canonical, robots, alternates: dict[str, str], og: dict[str, str])`; `landing(db, *, lang, city, district, specialty, page) -> LandingView`.
- [ ] **Step 1: Failing tests**: ru and tg pages render real masters (cards); ≥ 3 → robots index; < 3 → noindex; 0 → 200 noindex with "post a request"; district page counts only named-district masters for the threshold but lists whole-city ones; page 2 noindex + canonical with `?page=2`; hreflang ru/tg/x-default absolute URLs from SITE_URL; title format; neighbour links only to combinations with ≥ 1 master; `test_reserved_segments_never_collide_with_place_slugs` (Review Focus 3); path language overrides the cookie and the language switch links to the other-language URL.
- [ ] **Step 2–4:** RED, implement, GREEN + full checks.
- [ ] **Step 5: Commit** `feat(seo): city and specialty landing pages in ru and tg`

### Task 6: Meta tags on profiles/collections, robots.txt, X-Robots-Tag

**Files:**
- Modify: `app/web/profile.py`, profile and collection templates, `app/core/security_headers.py` (or a small middleware) for X-Robots-Tag, create `app/web/robots.py`
- Test: `tests/test_seo_meta.py`

- [ ] **Step 1: Failing tests**: profile/collection pages have title, description, canonical (absolute, SITE_URL), OG tags (image = avatar or first public thumb when present), no hreflang; draft pages keep noindex; `test_staging_sends_x_robots_tag_on_every_response` (Review Focus 5: page, 404, /media, /health); robots.txt: staging disallow all; production allow + disallows from spec + Sitemap line.
- [ ] **Step 2–4:** RED, implement, GREEN + full checks.
- [ ] **Step 5: Commit** `feat(seo): meta tags, robots.txt and no indexing outside production`

### Task 7: sitemap.xml and docs

**Files:**
- Create: `app/web/sitemap.py`, `app/services/sitemap.py`
- Modify: `docs/DECISIONS.md` (R-025 complete, with final thresholds), `docs/INFRASTRUCTURE.md` (search rebuild command after deploy; robots behaviour), `README.md` (endpoints), `docs/DATA_MODEL.md`
- Test: `tests/test_sitemap.py`

- [ ] **Step 1: Failing tests**: valid XML (parse with `xml.etree`), contains indexable SEO pages with ru/tg alternates, visible profiles, public collections; excludes < 3 pages, drafts, blocked-by-SQL owners (Review Focus 1), hidden collections; cache returns the same body within 10 minutes (monkeypatch time) and refreshes after; URL count guard.
- [ ] **Step 2–4:** RED, implement, GREEN + full checks.
- [ ] **Step 5: Commit** `feat(seo): sitemap.xml`
