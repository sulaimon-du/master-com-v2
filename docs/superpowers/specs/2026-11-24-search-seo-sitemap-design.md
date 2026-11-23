# Week 7: search, SEO pages and sitemap

Status: design, 2026-11-24. The owner handed product questions to Claude; choices not quoted from
BRIEF, PRODUCT_RULES or DECISIONS are **Claude's calls**, logged as R-025 for the owner to confirm.

## Why

BRIEF §10 week 7: "Search + SEO pages + sitemap — done when search tests are green". Clients find
masters by specialty and place (BRIEF §4.7); Google and Yandex bring them to generated landing
pages `/<city>/<specialty>` (BRIEF §4.14, R-010). Without search, published profiles are unreachable.

## What exists

- Published master profiles with specialties (`master_specialties`, primary flag), areas
  (`master_areas`: whole city or district), availability, `rating_score`, `created_at`, public
  collections with photos (weeks 5–6). Publication rule C1 (R-022). `public_master` re-checks owner
  active at read time; `published_at` stays set on a blocked owner (R-022 week-7 note).
- Catalog: 12 categories, 70 specialties, synonyms per specialty and language (`ru`, `tg`, `lat`);
  65 cities (cities and rural districts) and Dushanbe's 4 districts; all with ru/tg names and slugs.
- Slug transliteration (Russian + Tajik letters) in `app/services/masters.py`.
- `SITE_URL` setting; language from cookie `mc_lang` (ru default, tg); no URL-based language yet.
- Home page is a placeholder.

## Goals

1. **Search page** `/search` (BRIEF §4.7): filters specialty, city, district, free text (name or
   anything), availability; sorting relevance (default), rating, newest; 20 results per page; all
   state in the URL; result cards per design_direction "Search results" (avatar, name, headline,
   primary specialties, area, rating, a strip of up to 4 work photos, link to profile; no contacts).
2. **Matching**: typo tolerance (pg_trgm), transliteration Cyrillic ↔ Latin incl. ғ ӣ қ ӯ ҳ ҷ, specialty
   synonyms (ru/tg/lat), "did you mean" for a free-text query that matches no specialty/master well,
   and highlighted matches in names, headlines and specialties.
3. **Visibility** (BRIEF §4.7, C1, R-022): only published, not hidden profiles whose owner is active
   and has a verified phone. Imported/unclaimed profiles do not exist yet and are out of scope.
4. **SEO landing pages** (BRIEF §4.14, R-010): `/<city>/<specialty>` and
   `/<city>/<district>/<specialty>`, in Russian and Tajik, listing real masters (same cards),
   district filter links, neighbouring links (same specialty in nearby/other cities, other
   specialties in this city), a "post a request" call (links to a placeholder until weeks 8–9).
   Indexable only with ≥ 3 masters; otherwise `noindex`.
5. **Technical SEO**: `sitemap.xml`, `robots.txt`, canonical URLs, `hreflang` ru/tg/x-default,
   `<title>`, meta description and Open Graph tags on SEO pages, public profiles and collections.
6. **Staging must never be indexed.**
7. Home page gets the real search box (specialty + place) and category tiles.
8. Place synonyms for old names people still type (DECISIONS R-018 "before week 7").

## Non-goals

- Price range from actual requests (BRIEF §4.14): requests arrive in weeks 8–9; the block is left out
  until there is data (not an empty placeholder).
- Imported profiles, claim flow (week 11); map and geolocation (v2).
- A JSON search API, autocomplete widget beyond a plain `<datalist>`, client-side search.
- Background jobs (week 8): the sitemap is built on request with a short in-process cache.
- Ranking by distance; personalised ranking.

## Anti-goals

- A blocked owner's or a draft profile appearing anywhere in search, SEO pages or the sitemap.
- Contacts in any search or SEO HTML (guests and signed-in alike see no contacts on list pages).
- Staging pages in a search engine index.
- Thin or duplicate pages indexed (each page has one canonical; < 3 masters → noindex and not in
  the sitemap).
- A free-text query that can slow the database down (unbounded `%…%` scans, catastrophic regex).
- Any `|safe` for highlighting.

## Decisions (Claude's calls)

### D1. Search index: a denormalized row per master
A table `master_search` (one row per master profile, `master_id` PK FK CASCADE) holding:
`document` (text: display name, headline, bio, all specialty names in ru and tg and their synonyms,
area names ru/tg — all lowercased and **transliterated to Latin** with the slug transliteration),
`names` (lowercased transliterated name + headline, for name matching), `is_visible` (bool),
`rating_score`, `has_photos` (bool), `published_at`, and arrays/columns for filtering:
`specialty_ids int[]`, `city_ids int[]` (whole-city areas **and** the city of each district area),
`district_ids int[]`, `whole_city_ids int[]`, `is_available`.
- GIN trigram index on `document` and `names`; GIN on the int arrays.
- Rebuilt by `refresh_search(db, master)` called inside `refresh_publication` (so every profile and
  portfolio change keeps it current) and on block/unblock and phone verification paths that call
  `refresh_publication`. A CLI `python -m app.cli search rebuild` rebuilds all rows; run it once on
  staging after the deploy (production has no masters yet).
- `is_visible` = published and not hidden and owner active and phone verified. Search also joins
  `users` at query time to re-check `blocked_at IS NULL AND deleted_at IS NULL` (defence in depth for
  owners blocked by SQL/admin without a refresh).

Why not tsvector with a language config: Postgres has `russian` but no Tajik configuration; mixed
ru/tg/Latin input and typos are better served by one transliterated text with trigrams. Highlighting
is done in Python (D3), not by Postgres.

### D2. Query handling
- Free text `q`: trimmed, max 100 characters, lowercased, transliterated to Latin, split into at most
  6 words of ≥ 2 characters. Matching: every word must match the document by
  `word_similarity(word, document) >= 0.4` (pg_trgm `<%` operator with `pg_trgm.word_similarity_threshold`
  set per transaction). Relevance = sum of word similarities, + 0.3 if a word matches `names`,
  + 0.2 if the master has the filtered specialty as primary, + 0.1 if `has_photos`.
- If `q` resolves to a specialty (similarity ≥ 0.6 against a specialty name/synonym, transliterated),
  it also acts as a specialty filter suggestion: the page shows "Showing <specialty>" chips.
- "Did you mean": when a free-text search returns 0 results, suggest the best specialty or place name
  with similarity ≥ 0.3 (one suggestion, a link that sets that filter).
- Filters by slug in the URL: `?s=<specialty-slug>&cat=<category-slug>&c=<city-slug>&d=<district-slug>&q=…&available=1&sort=relevance|rating|newest&page=N`.
  Unknown slugs → ignored with a notice, never 500. `page` 1–50.
- A district filter matches masters serving that district or the whole city.
- Sorting: relevance (default when `q` is set; otherwise rating), rating (`rating_score` desc nulls
  last, then has_photos, then published_at desc), newest (`published_at` desc). Ties by master id.

### D3. Highlighting
Python marks matches: for each displayed string (name, headline, specialty names), find spans whose
transliterated form contains a query word (or is within edit distance 1 for words ≥ 5 chars) and
return a list of `(text, is_match)` segments; templates render `<mark>` around match segments with
autoescape. No `|safe`.

### D4. URLs and language for SEO pages
- Russian at `/<city>/<specialty>` and `/<city>/<district>/<specialty>`; Tajik at
  `/tg/<city>/<specialty>` and `/tg/<city>/<district>/<specialty>`. The path language overrides the
  cookie on these pages (and the language switch on them links to the other-language URL).
- Profiles and collections stay single-URL (language by cookie); their `hreflang` is omitted
  (canonical only).
- Routes are registered last; a path whose first segment is not a known active city slug → 404
  (no clash with existing routes because existing ones are registered first and city slugs never equal
  `m`, `master`, `auth`, `media`, `static`, `search`, `account`, `signin`, … — a test asserts no city
  or district slug collides with a reserved first segment).
- Canonical: the page's own clean URL (no query string). District filter links on a city page point to
  the district pages.

### D5. Indexing rules
- SEO page with ≥ 3 visible masters → `index, follow`, in the sitemap; fewer → `noindex, follow`,
  not in the sitemap, still reachable (helps users).
- Public profile → indexable when visible; collections → indexable when public.
- `/search` → `noindex, follow` (infinite combinations).
- **Staging/development**: every response gets `X-Robots-Tag: noindex, nofollow` and `robots.txt`
  disallows everything. Production `robots.txt`: allow all except `/search`, `/account`, `/master/`,
  `/auth/`, `/signin`, `/register`, `/password/`, `/confirm`; media stays crawlable for image search;
  plus `Sitemap: <SITE_URL>/sitemap.xml`.

### D6. Sitemap
`/sitemap.xml` (urlset, `xhtml:link` alternates for ru/tg SEO pages), built from the DB on request
and cached in process for 10 minutes; at most 50 000 URLs (a guard; we are far below). `lastmod` from
the newest `published_at`/`updated_at` of the listed masters. Only indexable URLs.

### D7. Meta tags
`<title>`: SEO page "<Specialty> в <городе> — N мастеров · UstoiMan" (tg equivalent); profile
"<Name> — <primary specialty>, <city> · UstoiMan". Meta description from headline/bio (≤ 160 chars,
plain text). Open Graph: title, description, url (canonical), type, image (profile avatar or the first
public photo thumbnail, else none), `og:locale` ru_RU / tg_TJ. All values autoescaped.

### D8. Place synonyms
A table `place_synonyms (id, city_id NULL, district_id NULL, lang, text)` seeded from
`app/seed/data/places.json` (`"synonyms"` lists per city/district, like specialties) with old and
alternative names (e.g. Курган-Тюбе → Бохтар, Ленинабад → Худжанд, Чкаловск → Бустон, Кайраккум →
Гулистон, Орджоникидзеабад → Вахдат, Душанбе's old district names where they exist). Used by the
free-text matcher (place words in `document`) and by "did you mean". Claude drafts the list; the owner
reviews it with the translations (TRANSLATIONS.md is for UI texts; place synonyms are reviewed in the
seed file).

### D9. Performance guards
- `statement_timeout` 2 s for search queries (SET LOCAL); a timeout → friendly "try a shorter query".
- Query words limited (D2); trigram threshold prevents full scans returning everything.
- Result query selects only visible rows via a partial index `WHERE is_visible`.
- Photo strips for 20 cards in one query (no N+1).

### D10. Home page
Search form (specialty `<select>` grouped by category with the free-text field, city `<select>`),
category tiles linking to `/search?cat=<category-slug>` (category filter = any of its specialties;
add `cat` to the D2 filters), "How it works" in 3 steps,
texts via locales. Keep it simple; design polish later.

### D11. Answers to the builder check (Claude's calls)
1. **Empty combinations:** an SEO page with 0 visible masters answers 200 with `noindex, follow`, a short
   text and the "post a request" call. Neighbouring, district and "other specialties" links point only
   to combinations with ≥ 1 visible master (counts from one grouped query). The sitemap lists only
   pages with ≥ 3 (D5).
2. **District pages vs the city page:** a district page *lists* masters serving that district or the
   whole city (what a user wants), but its **index threshold counts only masters who named that district**
   (≥ 3 of them → indexable; otherwise `noindex`). A city page lists and counts masters serving the whole
   city or any of its districts.
3. **Free text that resolves to a specialty** is a **hint, not a filter**: results still come from word
   matching; the page shows a chip "Search only in <specialty>" that links to the same URL with `s=` set.
   The "primary specialty" relevance bonus applies only when `s` is actually set. `d` without `c` is
   ignored (district slugs are unique only per city).
4. **SEO pagination:** 20 per page; page 2+ has `noindex, follow` and a self-referencing canonical
   including `?page=N`; page 1's canonical is the clean URL. The title count is the total.
5. **Sitemap contents:** SEO pages with ≥ 3 masters (ru and tg with `xhtml:link` alternates), visible
   profiles `/m/<slug>` (`lastmod` = profile `updated_at`), and public collections `/m/<slug>/c/<id>`
   (`lastmod` = newest visible photo `created_at`). Same 50 000 guard and 10-minute cache.

## Constraints
- Stack rules (CLAUDE.md): sync FastAPI, SQLAlchemy 2.0, Postgres 16 with `pg_trgm` (enable via
  migration), no new Python dependencies expected.
- Every list page goes through `render` (no-store for personalised pages; SEO and search pages carry
  no per-user data except the header — keep `no-store`, caching is a later optimisation).
- All texts in ru/tg locales; TRANSLATIONS.md regenerated.
- Tests on real Postgres; search behaviour tested with seeded catalog and created masters.

## Delivery
Two PRs:
- **PR A:** search index (migration with pg_trgm, `master_search`, place synonyms + seed),
  `refresh_search`, the search service, `/search` page, home page search box.
- **PR B:** SEO landing pages (ru/tg), meta/OG/canonical/hreflang, robots.txt, X-Robots-Tag on
  non-production, sitemap.xml, R-025 docs.

## Left to the builder
- Exact similarity thresholds may be tuned by tests (record final values in R-025).
- Card and page markup within design tokens variant 11.
- The neighbouring-links selection (keep it simple: up to 6 links each).
