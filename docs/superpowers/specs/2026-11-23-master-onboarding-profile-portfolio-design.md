# Weeks 5–6: master onboarding, profile, portfolio and the upload pipeline

Status: design, 2026-11-23. The owner handed product questions to Claude ("продуктовые вопросы
решаю сам — OK"); every choice below not quoted from BRIEF, PRODUCT_RULES or DECISIONS is
**Claude's call**, logged as R-022 for the owner to confirm. The owner also decided: storage behind
an interface with local disk now, R2 later; Claude may merge green, reviewed PRs.

## Why

BRIEF §10 weeks 5–6: "Master onboarding, profile, portfolio, upload pipeline, R2 — done when a
master can publish a portfolio". Masters are the supply side: without a profile and photos of
real work there is nothing to search (week 7) and nobody to respond to requests (weeks 8–9).

## What exists

- Tables from week 2 (migration 0002): `master_profiles` (slug, display_name, avatar_key,
  headline ≤80, bio ≤1000, experience 0–60, is_available, contacts, `published_at`, `hidden_at`,
  origin self/admin/import), `master_specialties` (is_primary), `master_areas` (whole city or one
  district), `portfolio_collections` (title, description, position, hidden_at),
  `portfolio_photos` (storage_key, thumb_key, width, height, position, `deleted_at` for undo).
- Catalog and places are seeded (`categories`/`specialties`, `cities`/`districts`).
- Phone verification through the Telegram bot (week 4): `users.phone` + `phone_verified_at`.
- Account page, sessions, CSRF, `render`, locales ru/tg, design tokens variant 11.

## Goals

1. A signed-in user presses "I am a master" and goes through a wizard (BRIEF §4.3): basics →
   specialties → work area → phone → first collection (skippable).
2. A public profile at `/m/<slug>` (BRIEF §4.4) with a square photo grid of collections; contacts
   only for signed-in users (BRIEF §4.6).
3. The master edits the profile and manages the portfolio: collections (create, edit, reorder,
   hide), photos (upload, reorder, delete with undo) within plan limits (BRIEF §4.5, §4.13).
4. One upload pipeline for avatars and portfolio, reused later by requests and chat (BRIEF §4.5).
5. Storage behind an interface; local disk now; R2 plugs in later by configuration.

## Non-goals

- Search, SEO pages, sitemap (week 7). The public profile exists but is not listed anywhere yet.
- R2 adapter, CDN, image purge job (later; the interface makes them a drop-in).
- Requests, responses, chat, reviews, trust signals beyond "on the site since" (weeks 8–10).
- Admin moderation UI (week 10). `hidden_at` is respected when set by SQL or the CLI.
- Claiming imported profiles (week 11). Companies and crews (v2).
- Drag-and-drop reordering (buttons are enough for v1).

## Anti-goals

- Any uploaded file served or stored as the user sent it (original bytes, metadata, GPS, name).
- A user able to change another master's profile, collection or photo by changing an ID.
- Contacts in the HTML for guests, even hidden.
- A master listed as published without a verified phone (BRIEF §4.2 "mandatory for masters").
- Photos that silently disappear on staging (Render's free disk is wiped on deploy).

## Decisions (Claude's calls unless quoted)

### D1. Who is a master
A user is a master when they own a `master_profiles` row (`origin='self'`). No role column.
Creating the profile is a privilege change: the session is rotated (R-011). The account page shows
"I am a master" (no profile) or "My master profile" (profile exists).

### D2. Onboarding wizard
Routes under `/master/onboarding`, one step per page, server-rendered forms (no SPA), each step
saved on submit so the master can leave and come back; a step indicator shows 1–5.

1. **Basics** — display name (prefilled from `users.name`), slug (prefilled per D4, editable,
   checked live with HTMX), avatar (optional, D7), headline ≤ 80, experience 0–60, availability.
   Submitting step 1 creates the profile (draft, `published_at` NULL) and rotates the session.
2. **Specialties** — the catalog grouped by its 12 directions; pick 1–10, mark up to 3 as primary
   (BRIEF §4.4). Server enforces the limits (plan limits from config).
3. **Work area** — cities; for a city with districts (Dushanbe), "whole city" or chosen districts.
   At least one area.
4. **Phone** — shows the verified phone if there is one; otherwise the Telegram link/verify
   button from week 4 (same POST `/account/telegram/link`, or the `?start=phone` link when Telegram
   is already linked) and a "Check again" button. Contacts: Telegram username, WhatsApp number,
   Instagram handle (all optional).
5. **First collection** — create a collection and upload photos, or "Skip".

After step 5 (or skip) the wizard calls `publish_if_ready` (D3) and lands on the profile page with
a notice: "published" or "almost done: <what is missing>".

### D3. Publishing (PRODUCT_RULES C1)
A profile is published (`published_at` set) only when all hold: verified phone on the user,
≥ 1 specialty, ≥ 1 area, not hidden, user active. Checked when the wizard finishes and after every
profile edit. If a condition stops holding (e.g. all specialties removed), `published_at` is
cleared. Photos are optional (C1: "photos optional but rank higher" — ranking is week 7).
A draft profile is visible only to its owner (the public URL answers 404 to others).

### D4. Slug (PRODUCT_RULES C2)
Auto-transliterated from the display name, editable. Transliteration covers Russian and the Tajik
letters (ғ→gh, ӣ→i, қ→q, ӯ→u, ҳ→h, ҷ→j); then lowercase, non `[a-z0-9]` runs → `-`, trimmed,
cut to 40; shorter than 3 → `master`; taken → suffix `-2`, `-3`, …. The live check (HTMX GET)
answers "free / taken / invalid" and suggests the next free one. Uniqueness is final at save
(unique index → friendly 409-style form error, never 500). Changing the slug later is allowed;
old URLs are not redirected in v1 (my call: no history table yet).

### D5. Contacts (BRIEF §4.4, §4.6)
- Phone contact is always the user's **verified** phone (not typed), so a profile cannot advertise
  someone else's number. `contact_phone` is set from `users.phone` at publish and kept in sync.
- Telegram: a username `[A-Za-z0-9_]{5,32}` (leading `@` stripped). WhatsApp: a phone, normalized
  with the week-4 `normalize_phone`. Instagram: a handle `[A-Za-z0-9._]{1,30}`.
- Rendered only to signed-in users; guests see "Sign in to see contacts" with `?next=` back to the
  profile. The guest HTML must not contain the values.

### D6. Plan limits (BRIEF §4.13)
From config, not code: `PLAN_LIMITS = {"free": {"collections": 30, "photos_per_collection": 60,
"specialties": 10, "primary_specialties": 3, "areas": 20}}` (areas limit is my call). Reaching a
limit gives a friendly message.

### D7. Upload pipeline (BRIEF §4.5)
One service `app/services/images.py`, used for avatars and portfolio (later requests and chat):
- Accept JPEG, PNG, WebP by **magic bytes** only; ignore filename and content type. ≤ 10 MB
  (checked while reading, not after). Reject anything else with a friendly message.
- Decompression-bomb guard: refuse images above 40 megapixels before decoding pixels.
- Apply EXIF orientation, convert to RGB, then re-encode: all metadata (EXIF, GPS, ICC comments,
  XMP) is dropped because nothing is copied over.
- Portfolio: WebP quality 80, long side 1600 px (never upscaled) + a 400 px thumbnail (long side).
- Avatar: center-cropped square, 400 px, WebP quality 80.
- Originals are never stored. Processing runs in the request (sync route, threadpool) — fine for
  v1 sizes.
- Storage keys are random and server-made: `p/<yyyy>/<mm>/<32 hex>.webp` and `…-t.webp`,
  `a/<32 hex>.webp`. Nothing from the user goes into a key.

### D8. Storage interface and serving
- `Storage` protocol: `put(key, data, content_type)`, `delete(key)`, `url(key) -> str`.
- `LocalDiskStorage(root)`: writes under `MEDIA_ROOT` (default `./media`, gitignored), atomic write
  (temp file + rename). Served by the app at `/media/{key}` with a strict key regex, no directory
  listing, `Cache-Control: public, max-age=31536000, immutable`, `X-Content-Type-Options: nosniff`,
  content type `image/webp`. CSP `img-src 'self'` already covers it.
- R2 later: an `R2Storage` with the same protocol and a public bucket URL; selected by settings.
- **Staging:** `UPLOADS_ENABLED` setting, default true in development/test and **false on
  staging/production until R2 exists** (render.yaml sets it). When false, avatar and photo upload
  controls are hidden and upload POSTs answer 404; everything else (profile, specialties, areas,
  publishing, empty collections) works. The owner can set it to true on staging to test with
  disposable photos (they vanish on the next deploy) — documented in INFRASTRUCTURE.md.

### D9. Portfolio management
- `/master/portfolio`: list of collections (cover = first photo), create, edit title/description,
  move up/down, hide/show.
- Collection page (owner): upload photos (multiple files in one form; each processed; per-file
  errors listed, the rest saved), move left/right, delete with "Undo" (soft delete via
  `deleted_at`; undo clears it; deleted photos are excluded everywhere and do not count toward the
  limit). Photos soft-deleted > 30 days are left for a later purge job.
- Positions are dense integers; moves swap neighbours in one transaction.

### D10. Public pages
- `/m/<slug>`: avatar, name, headline, availability, experience, "on the site since <month year>",
  specialties (primary first), areas, contacts (D5), bio, collections grid (square 1:1 tiles, cover
  photo, title). Only published, not hidden, owner active.
- `/m/<slug>/c/<collection_id>`: the collection gallery (design_direction "Project"): full photos in
  a horizontal scroll-snap strip, counter "3 / 12" via a small static Alpine CSP component,
  thumbnails below, description, master card. Hidden collections and deleted photos excluded.
- Owner viewing their own draft sees it with a "Draft — not public yet" banner.

### D11. Security checklist (for reviewers)
- Every write goes through a service that loads the master by the **current user**, never by an ID
  from the URL; collection/photo IDs are checked to belong to that master ("someone else tries"
  tests for every write route).
- CSRF on every POST and HTMX request; uploads are multipart POSTs with CSRF.
- Upload tests: wrong magic bytes with a `.jpg` name, 10 MB + 1 byte, a decompression bomb header,
  a JPEG with GPS EXIF → output has no EXIF, a rotated JPEG → output upright.
- `/media/` refuses `..`, absolute paths, and anything not matching the key regex.

## Constraints
- Stack and rules in CLAUDE.md: sync FastAPI, Jinja2 + HTMX + Alpine CSP build, no inline
  script/style, all text via locales (ru, tg), mobile first at 375 px, light and dark.
- New dependency: **Pillow** (already in the approved stack). No boto3 yet (R2 is out of scope;
  the owner pre-approved boto3 for when it is needed).
- Migrations only if something is missing (my expectation: one small migration at most).

## Delivery
Two PRs, each merged when CI is green and reviews are clean:
- **PR A:** upload pipeline + storage + `/media`, onboarding steps 1–4, profile edit, publishing,
  public profile (collections grid empty-state).
- **PR B:** portfolio management, onboarding step 5, collection gallery page.

## Left to the builder
- Exact templates and copy (ru; tg drafts for owner review), within design tokens variant 11.
- How the live slug check renders (HTMX fragment).
- Test fixtures for images (generate with Pillow in tests, no binary files committed).
