# Data model (v1)

Weeks 2–3. Source: `app/models/`. Migrations: `migrations/versions/*_0002_data_model.py` (data model), `*_0003_auth.py` and `*_0004_google_claims.py` and `*_0005_telegram.py` (sign-in), `*_0006_search.py` (search index, week 7). Choices not in the brief: R-018, R-019.

Conventions: `bigint` identity keys (small `int` for reference tables); timestamps are `timestamptz`; status fields are short strings with a named CHECK (not Postgres ENUMs); constraint names follow `app/core/db.py`. Nothing user-facing is hard-deleted: moderation sets `hidden_at`, photo deletion sets `deleted_at` (undo), account deletion clears personal columns and sets `users.deleted_at`. The one hard delete is "Remove my data" on an unclaimed profile: it takes the profile's complaints, claims, specialties, areas and portfolio with it (unclaimed profiles have no responses or reviews).

## Tables

| Table | What | Key rules enforced by the database |
|---|---|---|
| `users` | Accounts; email, Google and Telegram sign-in are all optional | email stored lowercase and unique; phone in E.164; a phone can be verified by one account only; locale `ru`/`tg`; `telegram_blocked_at` is set when the bot is blocked |
| `master_profiles` | Public master profile, linked to a user (nullable, for imported profiles) | slug `[a-z0-9-]{3,40}` unique; experience 0–60; one profile per user; plan `free`; `origin` self/admin/import with: self → has user, admin → has consent time (R-009), import → has source URL and import date (R-015); claimed → has user |
| `master_specialties` | Master ↔ specialty, `is_primary` | specialty in use cannot be deleted |
| `master_areas` | Work area: a whole city or one district | district must belong to the city; no duplicates |
| `categories` → `specialties` → `specialty_synonyms` | Catalog: 12 directions (BRIEF §5) | slugs unique; synonyms lowercase, unique per specialty and language (`ru`, `tg`, `lat`) |
| `cities` → `districts` | Places (R-018) | region and kind from fixed lists; district slug unique within its city |
| `requests` | Client's request (BRIEF §4.8) | district must belong to the city; budget amount and unit together, amount > 0; `timing = date` ⇔ date set; `master_chosen`/`completed` ⇔ chosen master; `completed` ⇔ `completed_at`; `closed`/`cancelled` ⇔ `closed_at` ⇔ `close_reason` (`client_closed`, `client_cancelled`, `auto_expired`, `auto_stale`); chosen master ⇔ `master_chosen_at`; `client_phone` in E.164 and required once a master is chosen; `feed_pause_at` > 0 (default 15); `opened_at` starts the C4 timer, `master_chosen_at` the C5 timer |
| `request_specialties`, `request_photos` | Specialties and up to 4 photos | photo positions 0–3, unique per request (so at most 4) |
| `responses` | Master's response | one per master per request; price amount and unit together; `withdrawn_at` = soft withdraw |
| `jobs` | Background work queue (weeks 8-9) | `kind` not blank; `attempts` ≥ 0; `max_attempts` > 0 (default 5); `dedupe_key` unique when set; partial index on `run_at` for jobs not done or failed; payload holds ids only |
| `notification_prefs` | Per-user opt-outs | key (user, event, channel); event and channel from fixed lists; a missing row means enabled; cascades with the user |
| `portfolio_collections`, `portfolio_photos` | Portfolio | title not blank; photo size positive; storage keys unique |
| `reviews` | Review of a completed request | one per request; rating 1–5; master reply and its time together |
| `complaints` | "Report" | exactly one target (profile, request or review); reason from the fixed list; `other` needs text; resolved ⇔ `resolved_at` |
| `support_threads`, `support_messages` | "Ask a question" (BRIEF §4.12a) | thread belongs to a user **or** a guest email; closed ⇔ `closed_at` |
| `profile_claims` | "This is me" on imported profiles | one pending claim per user and profile |
| `data_removal_requests` | "Remove my data" | resolved ⇔ `resolved_at` |
| `import_blocklist` | Never re-import removed people | only HMAC-SHA256 hex values (keyed with a server secret), unique per kind |
| `auth_sessions` | Signed-in browsers (week 3, R-019) | only the SHA-256 of the session ID is stored; cascades with the user |
| `auth_tokens` | One-time confirmation and reset links | purpose `confirm_email`, `reset_password` or `tg_link`; hash only; `used_at` makes each link single-use |
| `rate_limits` | Fixed-window counters (BRIEF §6.3) | key is a hash of scope and identity |
| `oauth_flows` | Google sign-in or link in progress | state hash, nonce and PKCE verifier; deleted when the callback runs |
| `google_claims` | Google user asking for an unconfirmed account with the same email (R-019) | status `pending`, `approved` or `rejected`; `resolved_at` set exactly when not pending; one pending claim per account and Google user; the handover needs the stored email unchanged; cascades with the user |
| `telegram_signins` | Telegram user waiting to finish signing in on the site | hash of the one-time token is unique; `expires_at` and `used_at` make it short-lived and single-use; indexed by `telegram_id` |
| `master_search` | One denormalized search row per master (week 7, spec D1): `document` and `names` lowercased and transliterated to Latin; visibility, availability, photos, `rating_score`, `published_at`; int arrays of specialty, category, city and district ids | cascades with the profile; arrays default to `{}` and are never null; GIN trigram indexes (`gin_trgm_ops`) on `document` and `names`, GIN on every array, partial btree on `rating_score` where `is_visible`. Needs the `pg_trgm` extension (created by migration 0006, kept on downgrade) |
| `place_synonyms` | Old and alternative names of a city or district (spec D8), languages `ru`, `tg`, `lat` | exactly one of city or district; text lowercase and not blank; unique per place, language and text; cascades with the place |

Weeks 5–6 PR A (master profiles, R-022) needed no schema change: `master_profiles`, `master_specialties` and `master_areas` from migration 0002 already hold everything.

## Rating (BRIEF §4.11)

`rating_score = (C · m + Σ ratings) / (C + n)`, with `C = 5` reviews of weight and prior mean `m = 4.0`. A newcomer with one 5 scores 4.17, which is below a master with 40 reviews averaging 4.8 (4.71). Recalculated by a background job; `rating_avg` keeps the plain average for display.

## Reference data

`python -m app.seed` loads `app/seed/data/`:
- `places.json`: cities and rural districts by region, plus Dushanbe's districts. A city or district may carry `"synonyms": {"ru": [...], "tg": [...], "lat": [...]}` (old names, Latin spellings); the seed makes `place_synonyms` equal to these lists. Drafted by Claude, for the owner's review;
- `categories.json`: directions, specialties and Russian synonyms;
- `tg_names.csv`: every Tajik name and Tajik synonyms, next to the Russian name, for the owner's review. Rows marked `?` in the `check` column are the ones I am least sure of.

The loader refuses to run if the two languages disagree (a missing, extra or renamed row).
