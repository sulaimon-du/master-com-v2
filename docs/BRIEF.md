# Master COM v2 brief

Status: draft 4, 2026-10-24. All items from draft 1 confirmed (R-011). Replaces `10_BRIEF.md`. Built from rounds 1–3 (`redefinition/round*_answers.md`) and `redefinition/decisions.md`.
Working name: **Master COM**. A new name with a Tajik root is pending (round 4).

---

## 1. Product

A web marketplace (PWA) for construction and home trades in Tajikistan: from architects and interior designers to plumbers and locksmiths.

It combines three things:
- **Profiles like LinkedIn:** who the master is, what they do, where they work, how long, what clients say.
- **Portfolios like Instagram:** a photo-first grid of real work.
- **An order exchange:** a client posts a request, masters respond, the client picks one.

**Goal:** a real business, not a study project. Free at launch; paid plans for masters later.

**Success at 3 months:**
- v1 is deployed and working.
- At least 10 real masters registered.
- Real clients have found a master through the site.

## 2. Market

- **Geography:** all of Tajikistan. Cities and districts live in lookup tables (seeded, editable in the admin panel).
- **Currency:** TJS only in v1.
- **UI languages:** Russian (default) and Tajik. Claude translates; the owner proofreads (native speaker). No English UI.
- **Search transliteration:** Cyrillic ↔ Latin, including Tajik letters ғ ӣ қ ӯ ҳ ҷ.

## 3. Roles

| Role | Can |
|---|---|
| Guest | Browse home, search, SEO pages, public profiles and portfolios. Cannot see contacts, post requests, respond or chat. |
| Client (registered user) | Everything a guest can, plus: see masters' contacts, post requests, pick a master, chat inside own requests, leave reviews on own completed requests, file complaints. |
| Master | A user who completed master onboarding **and verified a phone number**. Everything a client can, plus: public profile, portfolio, request feed, responding to requests. |
| Admin | The owner. Moderation, complaints, blocking, dictionaries, manual master onboarding. |

- **The server is the security boundary.** The UI only hides buttons. Every write checks ownership. No client sets its own role.
- One account can be a client and a master at the same time.
- **Companies and crews: v2.** v1 must not block them (masters are linked to users, not hard-wired as the only profile type).

## 4. v1 features

### 4.1 Sign-in
- **Email and password:**
  - Argon2id hashing;
  - email confirmation;
  - password reset by email;
  - login rate limit.
- **Google** (OIDC with `state`, PKCE and server-side `id_token` verification).
- **Telegram Login.** It provides no email, so the account may have no email.
- **Account linking.** A user signed in by any method can attach the other methods in settings. If a Google email already belongs to a password account, the user signs in with the password first, then links Google. No silent merging.
- **Sessions:** server-side sessions in Postgres instead of JWT. Safeguards:
  - the session ID is 256 random bits; only its hash is stored;
  - the cookie is `__Host-`-prefixed, `HttpOnly`, `Secure`, `SameSite=Lax`, so page scripts can't read it, even through an XSS hole;
  - a new ID is issued at sign-in and on privilege changes (prevents session fixation);
  - idle timeout 30 days, absolute lifetime 90 days;
  - logout deletes the server record at once; "sign out everywhere" deletes all of the user's sessions;
  - sessions of a blocked user are revoked immediately.
- **CSRF:** a token on every form and HTMX request, plus `SameSite`.

### 4.2 Phone verification
- **Main method (free):** the Telegram bot. The user taps "Share contact" in the bot. The phone is saved as verified only when the shared contact belongs to the same Telegram user.
- **Fallback:** a code via Telegram Gateway at $0.01 per delivered code. Paid, so it needs your approval before it is switched on.
- **Mandatory for masters** before they can respond or appear in search.
- **Optional for clients** in v1.

### 4.3 Onboarding
- After registration the user picks "I'm looking for a master" or "I am a master".
- **Master wizard:**
  1. basics: name, photo, headline, experience, availability;
  2. specialties;
  3. work area (cities and districts the master travels to);
  4. phone verification;
  5. first portfolio collection (can be skipped).

### 4.4 Master profile
- **Identity:**
  - display name and avatar;
  - slug `[a-z0-9-]{3,40}`, unique, checked live; profile URL `/m/<slug>`;
  - headline (≤ 80 characters) and bio (≤ 1000).
- **Work:**
  - experience, 0–60 years;
  - an availability switch;
  - **specialties: up to 10, of which up to 3 are primary;**
  - **work area:** a list of cities and districts.
- **Contacts:** phone, Telegram, WhatsApp, Instagram.
- **Trust signals:**
  - "on the site since…";
  - number of completed requests;
  - rating and reviews.
- **Plan field:** always "free" in v1 (see 4.13).

### 4.5 Portfolio
- **Limits:** up to 30 collections per master, up to 60 photos each.
- **Display:** a square grid; photos can be reordered; deleting a photo can be undone.
- **Upload pipeline** (shared by portfolio, requests and chat):
  - accepted: JPEG, PNG, WebP, checked by magic bytes; at most 10 MB per file;
  - EXIF orientation applied, then **all metadata stripped (including GPS)**;
  - converted to WebP at quality ~80, which keeps quality while shrinking files;
  - the long side is resized to 1600 px, plus a 400 px thumbnail;
  - stored in **Cloudflare R2**; processed on our server with Pillow.
  - Original files are not kept.

### 4.6 Contacts visibility
- Guests see "Sign in to see contacts". After sign-in the user returns to the same page.
- A client sees a master's contacts immediately.
- A master sees a client's contacts only after the client has picked them for a request.
- The exact address goes to the chosen master only when the client presses "Send address".

### 4.7 Search
- **Filters:** specialty, city, district, name or free text, availability.
- **Sorting:** relevance, rating, newest.
- **Matching:** typo tolerance and "did you mean" through Postgres full-text search and `pg_trgm`, plus transliteration and specialty synonyms.
- Matches are highlighted.
- Paginated; all search state lives in the URL.
- Only masters with a verified phone and not blocked are shown.

### 4.8 Requests (order exchange)
- **Who can post:** any registered user.
- **Fields:**
  - specialty, one or more (required);
  - city and district (required);
  - description (required);
  - up to 4 photos;
  - budget in TJS (optional): a fixed amount or a rate per m², running metre, piece or hour;
  - when: urgent / a date / doesn't matter;
  - exact address (hidden; see 4.6).
- **Responses:**
  - "Ready to take it" plus an optional short message and approximate price;
  - one response per master per request; never on their own request.
- **Response limit:** after **15 responses** the request leaves the masters' feed. The client can reopen it.
- **Lifecycle:** `open → master_chosen → completed` (then a review prompt). The client can also close or cancel the request at any stage before `completed`.
- **Masters' feed:** open requests matching the master's specialties and work area, newest first, with cursor pagination.

### 4.9 Chat
- Between a client and a master **inside one request**, after the master has responded.
- **Content:** text, photos (same pipeline as 4.5), and a "Send address" button.
- **Delivery:** the open chat polls the server every few seconds. New messages also trigger a Telegram notification, throttled so a burst of messages produces one notification. No WebSocket in v1.
- Messages can be reported (4.11).

### 4.10 Notifications
- **Channels:** the Telegram bot and email. Web push comes in v1.1.
- **Events:**
  - new matching request (to masters);
  - new response (to the client);
  - "you were chosen" (to the master);
  - new chat message;
  - request completed, please leave a review (to the client).
- Each user can turn each event off.
- **Sending:** a background worker sends them, not the web request (see 6.3).

### 4.11 Trust and moderation
- **Reviews:**
  - only the client of a `completed` request can leave one;
  - 1–5 stars plus text, one review per request.
- **Rating:** a Bayesian average, so a newcomer with one 5 doesn't outrank a master with 40 reviews at 4.8. The exact constants go in the data model spec.
- **"Report" button** on profiles, requests, messages and reviews.
- **Post-moderation:** content is published immediately, and the owner reviews it afterwards and on complaints.
- **"Verified" badge** after a manual document check: **v1.1**.

### 4.12 Admin panel (SQLAdmin)
- **Lists:** users, masters, requests, responses, reviews, complaints, categories, specialties and synonyms, cities and districts.
- **Actions:**
  - block a user;
  - hide a piece of content;
  - resolve a complaint;
  - add a master manually with a recorded consent (see 7);
  - import scraped masters, review the import, handle "Remove my data" requests (see 4.12b).
- **Access:** the admin panel sits behind admin-only authentication, separate from the public sign-in, with rate limiting.

### 4.12b Imported profiles and claiming (R-015)
- The admin panel imports masters from approved public sources (CSV or a scraper job), with a review step before publishing.
- Each imported record stores its source URL and import date.
- Unclaimed profiles are public and show a "Not yet confirmed by the master" label.
- **Claiming:** the master opens "This is me", verifies the phone through the Telegram bot; when it matches the imported phone, the profile moves to the master's account and the label disappears. A mismatch goes to manual review in the admin panel.
- **"Remove my data"** on every unclaimed profile; the owner handles it in the admin panel; removed people are never re-imported.
- Scraping respects each source's terms and robots.txt and runs at a low request rate. Only publicly shown data is imported: name, specialty, city or district, public contact; work photos only where the source allows reuse.
- Unclaimed profiles cannot respond to requests or use chat. Instead they show a **"Call"** button with the phone from the source (R-016):
  - like all contacts, the number is shown only to signed-in users (4.6); guests see "Sign in to see contacts";
  - each tap is counted; the count is shown to the master when they claim the profile ("12 clients called you here"), which is a reason to claim it;
  - if the number is reported as wrong, the profile is hidden until reviewed in the admin panel.

### 4.12a Support
- A support email in the footer.
- A "Ask a question" form. When the owner answers, the question turns into a temporary chat:
  - signed-in users get a thread in the site (polling, like request chat);
  - guests leave an email and get replies by email;
  - the owner answers in the admin panel and gets a Telegram alert for each new question;
  - a thread closes after 7 days without messages; a new question opens a new thread.

### 4.13 Monetization groundwork
- Each master has a `plan` field, `free` for everyone in v1.
- Plan limits (photos, collections and the like) are read from config, not hard-coded.
- No payments in v1. Later: local Tajik payment services and wallets, and bank cards.

### 4.14 SEO pages
- **Generated pages:**
  - `/<city>/<specialty>`;
  - `/<city>/<district>/<specialty>`;
  - in Russian and Tajik.
- **Content:**
  - real masters from the database;
  - the price range from actual requests;
  - district filters;
  - a call to post a request;
  - links to neighbouring pages.
- **Indexing:** a page is open to search engines only when it has **at least 3 masters**; otherwise it is `noindex`.
- **Technical:** `sitemap.xml`, canonical URLs, `hreflang` ru/tg, `<title>` and Open Graph tags.
- Public master profiles are indexed too.

### 4.15 Look
- Light and **dark theme in v1** (follows the device setting, with a manual switch).
- Soft rounded corners.
- Portfolio grid: square 1:1 tiles; tapping opens the full uncropped photo.
- Brand name written in Latin everywhere. Logo made by a designer from a brief.
- **Tentative pick (R-014):** the look of variant 6 "Typographic" (ink `#111111`, white cards, signal-yellow `#F5C400` accent, black buttons that turn yellow on hover, 12 px corners) with the typefaces of variant 2: Inter for the UI, Cormorant Garamond 700 for large headings only. Tokens: `redefinition/design_tokens.md`.

### 4.16 PWA
- A web app manifest and icons, so the site can be installed to the home screen.
- The service worker caches static assets and serves an offline page. Dynamic data is never cached.

## 5. Categories

- **Two levels:** direction → specialty, with synonyms for search.
- **The 12 directions in v1:**
  1. Architecture and design.
  2. Interior design.
  3. Construction: foundation, masonry, roofing, facade.
  4. Finishing and repair: plaster, tile, floors, ceilings.
  5. Plumbing.
  6. Electrical.
  7. Heating, ventilation, air conditioning.
  8. Windows, doors, locks.
  9. Custom furniture.
  10. Welding and metalwork.
  11. Landscaping and yard work.
  12. Post-renovation cleaning, debris removal, movers.
- The full specialty list with synonyms (ru, tg, transliterations) is a separate seed file; Claude drafts it and you review it.

## 6. Technology

### 6.1 Stack (R-001 – R-003)

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Web | FastAPI, **sync** routes only |
| Database | PostgreSQL, SQLAlchemy 2.0, Alembic |
| Pages | Jinja2 + HTMX + Alpine.js + Tailwind (CLI build) |
| Admin | SQLAdmin |
| Images | Pillow → Cloudflare R2 |
| Telegram bot | aiogram, separate process |
| Search | Postgres full-text search + `pg_trgm` |

No Rust, no separate SPA and no public API in v1.

### 6.2 Processes
- **Containers** (Docker Compose):
  - `web`: FastAPI;
  - `bot`: aiogram;
  - `worker`: background jobs;
  - `db`: Postgres;
  - `proxy`: Caddy, which handles automatic HTTPS.
- The same compose file runs locally with one command.

### 6.3 Background jobs
- **No Redis in v1.** Jobs live in a Postgres table, and the worker takes them with `SELECT … FOR UPDATE SKIP LOCKED`. This covers notifications, emails and periodic tasks (rating recalculation, sitemap) without an extra service on a small VPS.
- **Rate-limit counters** are kept in Postgres too.
- When load demands it, Redis is added without rewriting the business logic.

### 6.4 Project rules
- Routers only parse input, call a service and render a template. Business logic and permission checks live in services.
- Separate Pydantic models for input and output. Password hashes, internal flags and contacts never leak into templates or JSON.
- User content is always escaped. Never `|safe` on user data, never `x-html`.
- **Errors:** friendly pages for users; constraint violations return 409/422 and never 500.
- **Commits:** Conventional Commits.
- **Decisions:** every deviation from this brief is logged in `docs/DECISIONS.md`.

## 7. Data, privacy and the law

- **Law of the Republic of Tajikistan No. 1537 of 3 August 2018** "On the Protection of Personal Data". Its consent and dissemination articles still need checking.
  - **Scraping and publishing masters' profiles approved by the owner's lawyer** (R-015); the owner keeps the approval in writing. Safeguards in 4.12b.
  - Masters added by survey confirm consent with one tap in the Telegram bot; the date and time are stored.
- **Privacy page and terms** before launch.
- **User rights:** a user can delete their account. Personal data is removed, while reviews stay anonymized.
- **Photos:** all metadata stripped.
- **Backups:** nightly `pg_dump` to R2, kept 14 days; a monthly test restore.
- Logs go to stdout and are never written to disk on the server.

## 8. Infrastructure (R-007)

| Stage | Where | Cost |
|---|---|---|
| Before launch | Free staging | $0 |
| After launch | One EU VPS (~€6.5/month, 1 GB RAM) with Docker; move to 2 GB if memory is tight | ≤ $10 → ≤ $30 later |
| Images | Cloudflare R2 (no egress fees) | free tier at start |
| Domain | `.tj` (registration rules to be checked before purchase) | yearly fee |

- Any paid service needs your explicit approval.
- n8n is not in v1. It is a candidate for internal reports after launch.

## 9. Quality without manual review (R-008)

- **Mandatory tests:**
  - sign-in and sessions;
  - access control: no user can read or change another user's data;
  - contacts hidden from guests;
  - the request lifecycle;
  - chat permissions;
  - uploads;
  - search.
- **CI on every PR:**
  - lint (ruff);
  - tests against a Postgres container;
  - `alembic check`;
  - `pip-audit`.
  - A red CI blocks the merge.
- **Reviewer agent:** a separate Claude agent reviews every PR without having seen how it was written.
- **Weekly click-through:** you go through new features against a checklist.
- **Security audit** before launch.
- **Repository:** a new repository; the old one is archived.

## 10. Plan: 12 weeks at 12–20 h/week

| Weeks | Milestone | Done when |
|---|---|---|
| 1 | Skeleton: repo, compose, CI, `/health`, staging deploy | Deployed, CI green |
| 2 | Data model, migrations, seeds (categories, cities, districts) | Data model reviewed |
| 3–4 | Sign-in: email, Google, Telegram; sessions; Telegram bot with phone verification | Auth tests green |
| 5–6 | Master onboarding, profile, portfolio, upload pipeline, R2 | Master can publish a portfolio |
| 7 | Search + SEO pages + sitemap | Search tests green |
| 8–9 | Requests, responses, lifecycle, notifications, worker | Full request flow works |
| 10 | Chat, reviews, complaints, admin panel, profile import | Moderation and import work end to end |
| 11 | Claim flow, Tajik translation, PWA, privacy page, polishing | Native speaker approved; a master can claim an imported profile |
| 12 | Security audit, backups and restore test, production VPS, launch | Live on the `.tj` domain |

**If behind schedule, postpone in this order:** chat → web push → reviews.

## 11. Not in v1

- Companies and crews (v2).
- Map and geolocation.
- Web push and the "Verified" badge (v1.1).
- Payments.
- Public API and native apps.
- English UI.
- n8n.

## 12. Open questions

1. **Name** (round 6, with positioning). "Usto" is taken by usto.tj, a direct competitor. Visual style is tentatively chosen (R-014).
2. **Competitors:** usto.tj and hunar.tj in Tajikistan, mohir.tj (freelance) and ustabor.uz in Uzbekistan already exist. Positioning must state how we differ.
