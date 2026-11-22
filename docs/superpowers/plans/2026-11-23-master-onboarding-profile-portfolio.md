# Master onboarding, profile, portfolio and uploads: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user becomes a master through a 5-step wizard, gets a public profile at `/m/<slug>`, and publishes a portfolio of collections and photos processed by one safe upload pipeline.

**Architecture:** Sync services own every rule (`app/services/images.py`, `storage.py`, `masters.py`, `portfolio.py`); routers in `app/web/master.py`, `app/web/profile.py`, `app/web/portfolio.py` parse input and render. Storage is a protocol with a local-disk implementation served at `/media/{key}`. Existing week-2 tables are reused.

**Tech Stack:** FastAPI (sync), SQLAlchemy 2.0, Postgres 16, Jinja2 + HTMX + Alpine CSP build, Tailwind v4, Pillow (new, in the approved stack).

**Spec:** `docs/superpowers/specs/2026-11-23-master-onboarding-profile-portfolio-design.md`

## Global Constraints

- Two PRs: **PR A = Tasks 1–6** (branch `week-5-6-master`), **PR B = Tasks 7–9** (branch `week-5-6-portfolio` from main after PR A merges).
- Upload: JPEG/PNG/WebP by magic bytes only; ≤ 10 MB (10 × 1024 × 1024 bytes) checked while reading; refuse > 40 megapixels before decoding; EXIF orientation applied; all metadata dropped; WebP quality 80; portfolio long side 1600 px (never upscaled) + 400 px thumbnail (long side); avatar 400×400 center crop. Originals never stored.
- Keys: `p/<yyyy>/<mm>/<32 hex>.webp`, thumbnail `…-t.webp`, avatar `a/<32 hex>.webp`; nothing user-supplied in a key. `/media/{key}` accepts only `^(a|p/\d{4}/\d{2})/[0-9a-f]{32}(-t)?\.webp$`.
- `/media` responses: `Content-Type: image/webp`, `Cache-Control: public, max-age=31536000, immutable`, `X-Content-Type-Options: nosniff`.
- `UPLOADS_ENABLED`: default true in development/test, false when `APP_ENV` is staging/production unless set; when false, upload controls hidden and upload POSTs return 404.
- `PLAN_LIMITS = {"free": {"collections": 30, "photos_per_collection": 60, "specialties": 10, "primary_specialties": 3, "areas": 20}}` in settings.
- Slug `^[a-z0-9-]{3,40}$`; transliteration incl. ғ→gh, ӣ→i, қ→q, ӯ→u, ҳ→h, ҷ→j; fallback `master`; collisions `-2`, `-3`, …
- Publish iff: user active, user phone verified, ≥ 1 specialty, ≥ 1 area, profile not hidden. Re-evaluated after every profile change; cleared when no longer true.
- Contact phone = the user's verified phone (never typed). Telegram `[A-Za-z0-9_]{5,32}` (leading `@` stripped), WhatsApp via `normalize_phone`, Instagram `[A-Za-z0-9._]{1,30}` (leading `@` stripped).
- Contacts never in guest HTML. Draft profiles: 404 to everyone but the owner.
- Every write loads the master by the current user; IDs from URLs must belong to that master; "someone else tries" test per write route. CSRF on every POST (multipart included).
- Creating the master profile rotates the session (new session ID + CSRF cookie).
- All user-facing text via `app/locales/{ru,tg}.json` (tg drafts for owner review); no inline script/style; mobile first 375 px; light and dark.
- Checks before every commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -o addopts=""` (+ `uv run alembic check` when models change).

## Review Focus

1. **Truncated or corrupt image with valid magic bytes** → friendly "cannot read this image", never a 500. Test in Task 2.
2. **PNG with transparency / palette / 16-bit, CMYK JPEG** → processed to a visible RGB WebP, no crash. Test in Task 2.
3. **Slug edited to one that another master takes between check and save** → form error "taken", no 500. Test in Task 3.
4. **Master removes their last specialty or area after publishing** → profile unpublished, public URL 404. Test in Task 3.
5. **Two uploads into a collection at its limit (59 photos) at once** → never more than 60 non-deleted photos. Test in Task 7 (lock the collection row while counting).

---

### Task 1: Settings, Pillow, storage and `/media`

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add pillow`), `app/core/config.py`, `.env.example`, `.gitignore` (`media/`), `render.yaml` (both services: none needed for bot; site `UPLOADS_ENABLED: "false"` with comment), `app/main.py` (router)
- Create: `app/services/storage.py`, `app/web/media.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces: `Settings.media_root: str = "media"`, `Settings.uploads_enabled: bool` (validator: default by app_env as in Global Constraints), `Settings.plan_limits: dict[str, dict[str, int]]`, helper `Settings.limit(name: str, plan: str = "free") -> int`.
- Produces: `class Storage(Protocol): put(key: str, data: bytes, content_type: str) -> None; delete(key: str) -> None; url(key: str) -> str`; `class LocalDiskStorage(Storage)` (root path; atomic write via temp + `os.replace`; `delete` ignores missing); `KEY_RE` (the regex above); `get_storage() -> Storage` (cached, from settings); `new_key(kind: Literal["a", "p"], *, thumb_of: str | None = None) -> str`.
- Produces route `GET /media/{key:path}` named `media`.

- [ ] **Step 1: Failing tests**: `test_put_then_get_serves_webp_with_cache_headers`; `test_media_rejects_traversal_and_bad_keys` (`../x`, `/etc/passwd`, `a/zz.webp`, `p/2026/11/abc.png` → 404); `test_missing_key_is_404`; `test_delete_missing_is_silent`; `test_new_keys_match_regex_and_are_unique` (1000 keys, all distinct, thumb key = base + `-t.webp`); `test_uploads_disabled_by_default_on_staging` / `enabled_in_test`; `test_plan_limit_lookup` (`limit("photos_per_collection") == 60`).
- [ ] **Step 2: Run, expect FAIL** (ImportError).
- [ ] **Step 3: Implement.** Tests use a `media_root` fixture under `tmp_path` (monkeypatch `MEDIA_ROOT`, clear settings cache and `get_storage` cache).
- [ ] **Step 4: Run, PASS** + full checks.
- [ ] **Step 5: Commit** `feat(media): storage interface, local disk and /media route`

### Task 2: Image pipeline

**Files:**
- Create: `app/services/images.py`
- Test: `tests/test_images.py`

**Interfaces:**
- Consumes: Task 1 constants only (no storage here).
- Produces: `MAX_UPLOAD_BYTES = 10 * 1024 * 1024`, `MAX_PIXELS = 40_000_000`; `class ImageError(Exception)` with `.code: Literal["type", "size", "pixels", "unreadable"]`; `read_limited(stream: BinaryIO) -> bytes` (raises `ImageError("size")` as soon as > MAX); `@dataclass(frozen=True) class ProcessedImage: data: bytes; width: int; height: int`; `process_portfolio(data: bytes) -> tuple[ProcessedImage, ProcessedImage]` (full, thumb); `process_avatar(data: bytes) -> ProcessedImage`.

- [ ] **Step 1: Failing tests** (images generated with Pillow in the test; no binary fixtures):
  - `test_text_file_named_jpg_is_rejected` (code `type`), `test_gif_is_rejected` (`type`).
  - `test_eleven_megabytes_is_rejected_while_reading` (`read_limited` on a stream of 10 MiB + 1 → `size`).
  - `test_pixel_bomb_is_rejected_before_decoding` (PNG header claiming 20000×20000 → `pixels`).
  - `test_truncated_jpeg_is_unreadable` (first 200 bytes of a valid JPEG → `unreadable`) — Review Focus 1.
  - `test_gps_and_exif_are_removed` (JPEG with EXIF incl. GPS → output `Image.open(...).getexif()` empty and no `exif`/`icc_profile`/`xmp` in `.info`).
  - `test_exif_orientation_is_applied` (300×100 JPEG with Orientation=6 → output 100-wide-by-300-tall ratio).
  - `test_portfolio_sizes` (4000×3000 → full 1600×1200, thumb 400×300; 800×600 stays 800×600; outputs are WebP).
  - `test_avatar_is_square_400` (1000×500 → 400×400 WebP).
  - `test_png_with_alpha_palette_and_cmyk_jpeg_are_processed` — Review Focus 2.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** with Pillow: sniff magic bytes (`\xff\xd8\xff`, `\x89PNG\r\n\x1a\n`, `RIFF....WEBP`); `Image.open` then check `width*height` before `load()`; `ImageOps.exif_transpose`; convert to RGB (composite alpha on white); `thumbnail` (LANCZOS); save WebP quality 80 without `exif`/`icc_profile`; wrap Pillow errors as `unreadable`. Set `Image.MAX_IMAGE_PIXELS = MAX_PIXELS` module-wide.
- [ ] **Step 4: Run, PASS** + full checks.
- [ ] **Step 5: Commit** `feat(images): safe upload pipeline to WebP`

### Task 3: Master profile service

**Files:**
- Create: `app/services/masters.py`, `app/schemas/master.py`
- Modify: `app/services/auth.py` (add `Outcome.SLUG_TAKEN`, `Outcome.SLUG_INVALID`, `Outcome.LIMIT`) or a local enum in masters.py (builder's choice; keep one source)
- Test: `tests/test_masters_service.py`

**Interfaces:**
- Produces:
  - `slugify(name: str) -> str`; `suggest_slug(db, name: str) -> str`; `check_slug(db, slug: str, *, own_id: int | None = None) -> Literal["free", "taken", "invalid"]`.
  - `master_for_user(db, user: User) -> MasterProfile | None`.
  - `@dataclass class BasicsInput: display_name: str; slug: str; headline: str | None; experience_years: int | None; is_available: bool` (+ pydantic validation in `app/schemas/master.py`: name 1–80, headline ≤ 80, experience 0–60, bio ≤ 1000).
  - `create_profile(db, user: User, data: BasicsInput) -> MasterProfile` (raises `MasterError("slug_taken" | "exists")`), `update_basics(db, master, data, *, bio: str | None) -> None`.
  - `set_avatar(db, master, key: str) -> str | None` (returns old key to delete after commit).
  - `set_specialties(db, master, specialty_ids: list[int], primary_ids: list[int]) -> None` (raises `MasterError("limit" | "unknown" | "primary_not_selected")`).
  - `set_areas(db, master, whole_city_ids: list[int], district_ids: list[int]) -> None` (validates districts against cities, inactive refused, ≤ limit).
  - `set_contacts(db, master, *, telegram: str | None, whatsapp: str | None, instagram: str | None) -> None` (raises `MasterError("telegram" | "whatsapp" | "instagram")`).
  - `missing_for_publish(db, master) -> list[Literal["phone", "specialty", "area", "hidden", "inactive"]]`; `refresh_publication(db, master) -> bool` (sets/clears `published_at`, syncs `contact_phone` from the verified phone).
  - `ProfileView` (pydantic, in schemas): slug, display_name, avatar_url, headline, bio, experience_years, is_available, since (date), specialties (list of `(name, primary)` in the viewer's language), areas (list of names), contacts (`ContactsView | None` — None for guests), is_draft, collections (empty list in PR A; Task 9 fills).
  - `profile_view(db, master, *, lang: str, viewer: User | None) -> ProfileView`; `public_master(db, slug: str, viewer: User | None) -> MasterProfile | None` (draft/hidden/inactive → None unless viewer owns it).
- [ ] **Step 1: Failing tests** (real DB, seeded catalog/places): `test_slugify_cyrillic_and_tajik` (`"Ҷамшед Қодиров"` → `"jamshed-qodirov"`, `"Иван Петров"` → `"ivan-petrov"`, `"!!"` → `"master"`, 60-char name → ≤ 40); `test_suggest_slug_adds_suffix`; `test_check_slug_states`; `test_create_profile_and_second_is_refused`; `test_slug_taken_at_save_is_error_not_500` (insert conflicting slug between check and save) — Review Focus 3; `test_specialty_limits` (11 → limit; 4 primary → limit; primary not in selected → error; unknown id → error); `test_areas_district_must_belong_to_city`; `test_contacts_validation` (`@my_name` ok, `ab` refused, WhatsApp `992 90 123 45 67` → `+992901234567`, Instagram `@shop.tj` ok); `test_publish_needs_phone_specialty_area` (each missing listed; all present → published, `contact_phone` = verified phone); `test_unpublished_when_last_specialty_removed` — Review Focus 4; `test_blocked_owner_profile_is_not_public`; `test_contacts_none_for_guest_view`; `test_draft_visible_only_to_owner`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Transliteration table for Russian (standard, `щ→shch`, `ж→zh`, `х→kh`, `ц→ts`, `ч→ch`, `ш→sh`, `ю→yu`, `я→ya`, `ё→yo`, `й→y`, `ъ/ь→""`, `ы→y`, `э→e`) plus the Tajik letters in Global Constraints. Slug uniqueness final via the DB unique index inside `begin_nested`.
- [ ] **Step 4: Run, PASS** + full checks.
- [ ] **Step 5: Commit** `feat(masters): profile, specialties, areas, contacts and publishing`

### Task 4: Onboarding wizard steps 1–4 and profile editing

**Files:**
- Create: `app/web/master.py`, templates `app/templates/pages/master/onboarding_basics.html`, `onboarding_specialties.html`, `onboarding_area.html`, `onboarding_phone.html`, `edit.html`, partial `app/templates/partials/slug_check.html`, `partials/wizard_steps.html`
- Modify: `app/main.py`, `app/templates/pages/account.html` (entry button), `app/locales/{ru,tg}.json`
- Test: `tests/test_master_web.py`

**Interfaces:**
- Consumes: Task 3 services; Task 1/2 storage and images for the avatar; `auth.open_session` + `_attach_session` for rotation; week-4 `telegram_link` route and `telegram_phone_url` context for step 4.
- Produces routes (all require sign-in; guests → `/signin?next=…`): `GET/POST /master/onboarding/basics` (`master_basics`), `GET /master/slug-check?slug=` (`master_slug_check`, HTMX fragment), `GET/POST /master/onboarding/specialties`, `GET/POST /master/onboarding/area`, `GET/POST /master/onboarding/phone` (POST saves contacts and finishes: `refresh_publication`, redirect to `/m/<slug>?notice=published|incomplete`), `POST /master/avatar` (multipart, CSRF; 404 when uploads disabled), `GET /master/edit` (all sections on one page, each section its own form posting to the same step routes with `?next=edit`).
- [ ] **Step 1: Failing tests**: guest redirected; `test_basics_creates_profile_and_rotates_session` (new `mc_session` and `mc_csrf`); steps refuse a user without a profile (redirect to basics); `test_slug_check_fragment` (free/taken/invalid text); `test_specialties_and_area_forms_save` ; `test_phone_step_finishes_and_publishes_with_verified_phone` / `…shows_missing_without_phone`; `test_avatar_upload_stores_webp_and_replaces_old` (old file deleted); `test_avatar_upload_404_when_uploads_disabled`; `test_avatar_bad_file_shows_friendly_error`; `test_csrf_required_on_every_post` (existing CSRF route test must pass); **someone else tries:** user B cannot change user A's profile by any route (all routes act on the current user only — assert A unchanged after B posts).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** thin routes; templates in variant-11 style (pills, cards); the specialties form groups by category with checkboxes and a "primary" checkbox per item; area form: city checkboxes, districts shown under cities that have them (no JS needed; plain form). Texts in both locales.
- [ ] **Step 4: Run, PASS** + full checks + `npm run build` if new classes (CI also builds).
- [ ] **Step 5: Commit** `feat(masters): onboarding wizard and profile editing`

### Task 5: Public profile page

**Files:**
- Create: `app/web/profile.py`, `app/templates/pages/master/profile.html`
- Modify: `app/main.py`, locales
- Test: `tests/test_profile_web.py`

**Interfaces:**
- Consumes: `public_master`, `profile_view` (Task 3).
- Produces route `GET /m/{slug}` (`master_profile`), notices `published` / `incomplete` (incomplete lists what is missing via `missing_for_publish`).
- [ ] **Step 1: Failing tests**: `test_published_profile_is_public`; `test_draft_is_404_for_others_and_visible_to_owner_with_banner`; `test_hidden_profile_404`; `test_guest_sees_sign_in_prompt_and_no_contact_values` (assert phone, telegram, whatsapp, instagram strings absent from guest HTML) ; `test_signed_in_user_sees_contacts`; `test_sign_in_link_returns_to_profile` (`next=/m/<slug>`); `test_specialties_in_viewer_language_primary_first`; `test_no_store_not_required_but_contacts_page_not_cached_for_guests` → simpler: assert `Cache-Control: no-store` on every profile response (it varies by viewer).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, PASS** + full checks.
- [ ] **Step 5: Commit** `feat(masters): public profile page`

### Task 6: PR A docs and decisions

**Files:**
- Modify: `docs/DECISIONS.md` (R-022 from the spec's D1–D11, marked "Claude's call, owner to confirm"; include the Pillow dependency reason), `docs/INFRASTRUCTURE.md` (`UPLOADS_ENABLED`, `MEDIA_ROOT`; how to test photos on staging and that they vanish on deploy), `docs/DATA_MODEL.md` (note: no schema change, or the migration if Task 3 needed one), `README.md` (endpoints), `docs/PRODUCT_RULES.md` (C1, C2 status: implemented)
- Test: none new; run full checks.
- [ ] **Step 1: Write the docs.**
- [ ] **Step 2: Run** full checks (docs only, suite must stay green).
- [ ] **Step 3: Commit** `docs: R-022 and infrastructure notes for master profiles`

### Task 7: Portfolio service (PR B)

**Files:**
- Create: `app/services/portfolio.py`
- Modify: `app/schemas/master.py` (`CollectionCard`, `PhotoView`)
- Test: `tests/test_portfolio_service.py`

**Interfaces:**
- Consumes: Task 1 storage, Task 2 images, Task 3 `master_for_user`, `Settings.limit`.
- Produces:
  - `create_collection(db, master, title: str, description: str | None) -> PortfolioCollection` (limit 30 → `PortfolioError("limit")`; title 1–120 after strip; description ≤ 1000).
  - `update_collection(db, master, collection_id: int, *, title, description) -> None`; `set_collection_hidden(db, master, collection_id, hidden: bool) -> None`; `move_collection(db, master, collection_id, direction: Literal["up", "down"]) -> None`.
  - `add_photo(db, storage, master, collection_id: int, data: bytes) -> PortfolioPhoto` (locks the collection row `FOR UPDATE`, counts non-deleted photos, limit 60 → `PortfolioError("limit")`; processes via `process_portfolio`; writes both files; on DB failure deletes the written files).
  - `move_photo(db, master, photo_id, direction: Literal["left", "right"]) -> None`; `delete_photo(db, master, photo_id) -> None` (sets `deleted_at`); `undo_delete_photo(db, master, photo_id) -> None` (only if within 30 days and limit allows).
  - `collections_for_owner(db, master) -> list[CollectionCard]`; `public_collections(db, master) -> list[CollectionCard]` (not hidden, ≥ 1 visible photo, cover = first by position); `collection_photos(db, master, collection_id, *, owner: bool) -> list[PhotoView]`.
  - Every function resolves `collection_id`/`photo_id` **through** `master` (join on `master_id`); a foreign ID raises `PortfolioError("not_found")`.
- [ ] **Step 1: Failing tests**: create/edit/hide/move; `test_collection_limit_30`; `test_photo_limit_60_with_concurrent_uploads` (two sessions, collection at 59, both add → exactly one succeeds) — Review Focus 5; `test_deleted_photo_excluded_and_undo_restores`; `test_undo_refused_when_limit_full`; `test_moves_swap_positions_and_stay_dense`; `test_foreign_collection_and_photo_ids_are_not_found` (master B with A's IDs, every function); `test_files_removed_when_db_write_fails`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, PASS** + full checks.
- [ ] **Step 5: Commit** `feat(portfolio): collections and photos service`

### Task 8: Portfolio management pages and onboarding step 5

**Files:**
- Create: `app/web/portfolio.py`, templates `app/templates/pages/master/portfolio.html`, `collection_edit.html`, `onboarding_collection.html`
- Modify: `app/web/master.py` (step 4 now goes to step 5; step 5 finishes; "Skip" finishes), `app/main.py`, locales
- Test: `tests/test_portfolio_web.py`

**Interfaces:**
- Consumes: Task 7 service.
- Produces routes (sign-in + own profile required): `GET /master/portfolio`, `POST /master/portfolio/collections`, `GET/POST /master/portfolio/collections/{id}` (edit), `POST …/{id}/hide`, `POST …/{id}/move?dir=up|down`, `POST …/{id}/photos` (multipart, `multiple`; per-file errors listed; 404 when uploads disabled), `POST /master/portfolio/photos/{id}/move?dir=left|right`, `POST …/photos/{id}/delete` (HTMX: returns an "Undo" fragment), `POST …/photos/{id}/undo`; `GET/POST /master/onboarding/collection` (create + upload, or `skip`).
- [ ] **Step 1: Failing tests**: happy paths for each route; `test_multi_upload_reports_bad_files_and_keeps_good_ones`; `test_upload_routes_404_when_uploads_disabled` (collections still work); someone-else-tries for every route (B posts to A's collection/photo IDs → 404, A unchanged); `test_delete_then_undo_via_htmx`; onboarding step 5 skip and create both publish via `refresh_publication`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, PASS** + full checks.
- [ ] **Step 5: Commit** `feat(portfolio): management pages and onboarding step 5`

### Task 9: Public collections on the profile and the gallery page

**Files:**
- Create: `app/templates/pages/master/collection.html`, `app/static/js/gallery.js` (Alpine CSP component `gallery`: counter "n / total" from scroll position), `assets/css` additions if needed
- Modify: `app/web/profile.py` (`GET /m/{slug}/c/{collection_id}` named `master_collection`), `app/services/masters.py` `profile_view` fills `collections` from `public_collections`, `profile.html` grid, `docs/DECISIONS.md` R-022 (portfolio items), `README.md`
- Test: `tests/test_profile_web.py`

**Interfaces:**
- Consumes: Task 7 `public_collections`, `collection_photos`.
- [ ] **Step 1: Failing tests**: `test_profile_shows_square_grid_of_public_collections` (hidden and empty collections absent; cover is the first photo's thumb URL); `test_collection_page_lists_photos_in_order_without_deleted`; `test_hidden_collection_404`; `test_collection_of_draft_master_404_for_others`; `test_collection_page_has_no_inline_script` (no `<script>` without `src`, no `style=`).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Gallery: CSS scroll-snap horizontal strip of full images (`loading="lazy"`, width/height attributes), thumbnails row linking to `#photo-n` anchors; counter via the Alpine component registered in `gallery.js` (CSP build: `Alpine.data`).
- [ ] **Step 4: Run, PASS** + full checks.
- [ ] **Step 5: Commit** `feat(portfolio): public collections and gallery page`
