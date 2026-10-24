# Data model (v1)

Week 2. Source: `app/models/`. Migration: `migrations/versions/*_0002_data_model.py`. Choices not in the brief: R-018.

Conventions: `bigint` identity keys (small `int` for reference tables); timestamps are `timestamptz`; status fields are short strings with a named CHECK (not Postgres ENUMs); constraint names follow `app/core/db.py`. Nothing user-facing is hard-deleted: moderation sets `hidden_at`, photo deletion sets `deleted_at` (undo), account deletion clears personal columns and sets `users.deleted_at`. The one hard delete is "Remove my data" on an unclaimed profile: it takes the profile's complaints, claims, specialties, areas and portfolio with it (unclaimed profiles have no responses or reviews).

## Tables

| Table | What | Key rules enforced by the database |
|---|---|---|
| `users` | Accounts; email, Google and Telegram sign-in are all optional | email stored lowercase and unique; phone in E.164; a phone can be verified by one account only; locale `ru`/`tg` |
| `master_profiles` | Public master profile, linked to a user (nullable, for imported profiles) | slug `[a-z0-9-]{3,40}` unique; experience 0–60; one profile per user; plan `free`; `origin` self/admin/import with: self → has user, admin → has consent time (R-009), import → has source URL and import date (R-015); claimed → has user |
| `master_specialties` | Master ↔ specialty, `is_primary` | specialty in use cannot be deleted |
| `master_areas` | Work area: a whole city or one district | district must belong to the city; no duplicates |
| `categories` → `specialties` → `specialty_synonyms` | Catalog: 12 directions (BRIEF §5) | slugs unique; synonyms lowercase, unique per specialty and language (`ru`, `tg`, `lat`) |
| `cities` → `districts` | Places (R-018) | region and kind from fixed lists; district slug unique within its city |
| `requests` | Client's request (BRIEF §4.8) | district must belong to the city; budget amount and unit together, amount > 0; `timing = date` ⇔ date set; `master_chosen`/`completed` ⇔ chosen master; `completed` ⇔ `completed_at`; `closed`/`cancelled` ⇔ `closed_at` |
| `request_specialties`, `request_photos` | Specialties and up to 4 photos | photo positions 0–3, unique per request (so at most 4) |
| `responses` | Master's response | one per master per request; price amount and unit together |
| `portfolio_collections`, `portfolio_photos` | Portfolio | title not blank; photo size positive; storage keys unique |
| `reviews` | Review of a completed request | one per request; rating 1–5; master reply and its time together |
| `complaints` | "Report" | exactly one target (profile, request or review); reason from the fixed list; `other` needs text; resolved ⇔ `resolved_at` |
| `support_threads`, `support_messages` | "Ask a question" (BRIEF §4.12a) | thread belongs to a user **or** a guest email; closed ⇔ `closed_at` |
| `profile_claims` | "This is me" on imported profiles | one pending claim per user and profile |
| `data_removal_requests` | "Remove my data" | resolved ⇔ `resolved_at` |
| `import_blocklist` | Never re-import removed people | only HMAC-SHA256 hex values (keyed with a server secret), unique per kind |

## Rating (BRIEF §4.11)

`rating_score = (C · m + Σ ratings) / (C + n)`, with `C = 5` reviews of weight and prior mean `m = 4.0`. A newcomer with one 5 scores 4.17, which is below a master with 40 reviews averaging 4.8 (4.71). Recalculated by a background job; `rating_avg` keeps the plain average for display.

## Reference data

`python -m app.seed` loads `app/seed/data/`:
- `places.json`: cities and rural districts by region, plus Dushanbe's districts;
- `categories.json`: directions, specialties and Russian synonyms;
- `tg_names.csv`: every Tajik name and Tajik synonyms, next to the Russian name, for the owner's review. Rows marked `?` in the `check` column are the ones I am least sure of.

The loader refuses to run if the two languages disagree (a missing, extra or renamed row).
