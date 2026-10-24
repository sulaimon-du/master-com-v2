# Master COM redefinition: decisions log

Supersedes the old 14_DECISIONS.md. The new brief is `redefinition/BRIEF.md`. Numbering R-xxx to avoid clashing with D-001..D-100.

## R-001 Backend stack (2026-10-22)
- Python + FastAPI, **sync** routes, SQLAlchemy 2.0 + Alembic, PostgreSQL.
- No async/sync mixing inside the web app.
- No Rust in v1. Revisit only if production profiling shows a concrete bottleneck; then a single PyO3 module or a small separate service.
- Rejected: Django (user's choice), Go (slower to v1: admin and more glue hand-written), Rust (second language, no bottleneck to justify it).

## R-002 Frontend (2026-10-22)
- Server-rendered Jinja2 templates + HTMX + Alpine.js + Tailwind (compiled with CLI). One application, PWA.
- No separate SPA. No public API in v1; JSON endpoints added when a mobile app is needed.

## R-003 Supporting libraries (2026-10-22)
- Admin: SQLAdmin.
- Images: Pillow.
- Telegram bot: aiogram, run as a **separate process/container** (it is async-only). The web app sends notifications with plain sync HTTP calls to the Telegram Bot API, so the "no mixing" rule holds per process.

## R-004 Scope and time (round 3)
- Full v1 scope in 3 months at 12–20 h/week.
- Reviews ship in v1 (code ready; reviews appear with the first completed requests).
- If behind schedule, postpone in this order: chat → web push → reviews.

## R-005 Chat (round 3)
- Only between client and master inside a request, after a response.
- v1 content: text, photos, "send address" button.
- Delivery: polling every few seconds while the chat is open + Telegram notification. No WebSocket in v1.
- Chat photos are compressed (WebP, long side 1600 px, EXIF/GPS stripped) like portfolio photos.

## R-006 Moderation (round 3)
- Post-moderation: profiles and requests publish immediately; owner reviews after and on complaints.
- Single moderator (owner).

## R-007 Hosting and data (round 3)
- Before launch: free staging. After launch: one EU VPS (~€6.5/month) with Docker.
- Postgres on the same VPS, nightly backups to object storage, restore tested.
- Images: Cloudflare R2, processed on our server.
- Domain: .tj (foreigners may register through a local registrar; a-z, 0-9, hyphen, 2–63 chars). Defensive .com too, if cheap.

## R-008 Quality without manual review (round 3)
- Mandatory tests on auth, access control, requests, chat.
- CI blocks merge on failing tests, lint or vulnerability scan.
- A separate Claude agent reviews every PR without having seen the code being written.
- Weekly click-through of new features by the owner against a checklist.
- Separate security audit before launch.
- New repository; the old one stays as an archive.

## R-009 Consent and seeding (2026-10-22)
- Consent of surveyed masters is recorded with a one-tap confirmation in the Telegram bot; date and time stored in the database.
- Scraping and publishing other people's data were on hold until a lawyer in Dushanbe was consulted (Law No. 1537 of 3 Aug 2018 "On the Protection of Personal Data"). *Superseded by R-015.*

## R-010 SEO pages in v1 (2026-10-22)
- Generated landing pages `/<city>/<specialty>` and `/<city>/<district>/<specialty>`, in ru and tg.
- Indexed only with ≥ 3 masters; otherwise `noindex`. Sitemap, canonical, hreflang, OG tags.

## R-011 Platform choices confirmed (2026-10-22)
- **Sessions instead of JWT**, with safeguards: 256-bit random ID stored hashed; `__Host-` cookie, HttpOnly, Secure, SameSite=Lax; new ID at sign-in and privilege change; 30-day idle / 90-day absolute lifetime; instant logout and "sign out everywhere"; blocked users' sessions revoked at once; CSRF token on every form and HTMX request.
- Postgres-backed job queue (`FOR UPDATE SKIP LOCKED`) and rate-limit counters; no Redis in v1.
- Caddy as reverse proxy with automatic HTTPS.
- Account deletion removes personal data; reviews stay anonymized.
- Nightly backups kept 14 days; monthly restore test.
- Phone verification optional for clients, mandatory for masters.

## R-012 Look and language (round 4)
- Soft rounded corners. Portfolio: square 1:1 grid, full uncropped photo on tap.
- Dark theme ships in v1 (device setting + manual switch).
- Logo by a designer; Claude writes the designer brief once the name is chosen.
- Brand name in Latin everywhere.
- The owner proofreads Tajik names and the UI translation.
- "Usto" rejected: usto.tj is a live service for finding repair masters in Tajikistan (direct competitor).

## R-013 Open questions resolved (2026-10-22)
- All proposals in `open_questions.md` approved, with these changes:
  - C3: all price units allowed: fixed, per m², per running metre, per piece, per hour.
  - D1/D2: owner runs two design research prompts (`design_research_prompts.md`); round 5 screens follow the findings.
  - E3: no registered business for now.
  - E4: support = email + in-site support question that becomes a temporary chat once the owner answers:
    - signed-in users: thread in the site, polling like request chat; guests: leave an email, replies go by email;
    - owner answers in the admin panel and gets a Telegram alert for each new question;
    - thread closes after 7 days without messages; a new question opens a new thread.
- Competitor analysis skipped by the owner's decision. Positioning moved to round 6.
- "master" as a name: generic word, weak as a trademark and in search; kept as working name; final name in round 6. Domain status of master.tj / mastercom.tj not verified (owner checks via a .tj registrar whois).

## R-014 Visual direction, tentative (2026-10-24)
- Owner's current pick: **variant 6 "Typographic" look** (ink `#111111`, white cards, signal-yellow `#F5C400` accent, black buttons turning yellow on hover, 12 px corners) **with the typefaces of variant 2 "Magazine"**: Inter for the UI, Cormorant Garamond (700) for headings.
- Marked tentative ("for now"); added to the design lab as variant 11 "Pick: 6 + fonts of 2" and set as its default.
- Risks to watch:
  - Cormorant Garamond is thin at small sizes, so keep it for large headings (H1, hero, project titles) and use Inter for small headings;
  - large type takes space from photos on phones.

## R-015 Seeding by scraping approved (2026-10-24)
- The owner's lawyer approved scraping and publishing masters' profiles (supersedes the hold in R-009). The owner keeps the approval in writing, with its scope.
- Flow, per round 2: scraped profiles are published, and the master claims the profile by verifying the phone.
- Safeguards built in:
  - each imported record stores its source URL and import date;
  - unclaimed profiles carry a "Not yet confirmed by the master" label;
  - a "Remove my data" request on every unclaimed profile, handled in the admin panel; removed people are never re-imported;
  - scraping respects each source site's terms and robots.txt, at a low request rate;
  - imported data is limited to what the source shows publicly: name, specialty, city or district, public contact; work photos only where the source allows reuse.
- Scope: an import tool in the admin panel plus the claim flow, about 3–5 extra days, placed in weeks 10–11.

## R-016 Contact for unclaimed profiles (2026-10-24)
- Unclaimed (imported) profiles show a "Call" button with the phone from the source, instead of responses and chat.
- The number is visible to signed-in users only, like all contacts.
- Taps are counted and shown to the master on claiming; a "wrong number" report hides the profile until admin review.

## Open
- Final name and positioning (round 6): postponed by the owner; working name Master COM stays.
- Visual direction: tentative pick in R-014; final confirmation pending.
- Logo designer: postponed by the owner.
- n8n: not in v1; candidate for post-launch internal ops automation.
