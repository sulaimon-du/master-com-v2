# Master COM: approved product and process rules

Originally the open-questions list (2026-10-22). All proposals below are **approved** (R-013), with the changes listed in the status block. Treat each "Proposal" column as the rule. Later decisions in `DECISIONS.md` override this file.

**Status 2026-10-22:** all proposals approved except the changes below (see R-013 in `decisions.md`).
- C3: all price units allowed (fixed, m², running metre, piece, hour).
- D1, D2: owner runs two research prompts (`design_research_prompts.md`) before round 5.
- E3: no registered business for now.
- E4: support by email + a support question that becomes a temporary chat once answered.

Each item has my proposed default. "Needed by" refers to the 12-week plan in `BRIEF.md` §10.

## A. Infrastructure and tools

| # | Question | Proposal | Needed by |
|---|---|---|---|
| A1 | Free staging host | Pick after checking current free tiers | Week 1 |
| A2 | Repository | Private GitHub repo under the working name; rename when the brand is chosen | Week 1 |
| A3 | Error monitoring | Sentry free plan | Week 1 |
| A4 | Transactional email (confirmations, password reset) | A service with a free tier; I compare and verify limits | Week 3 |
| A5 | Telegram Gateway (paid fallback for phone codes) | Off until the free bot flow proves insufficient | Week 4 |
| A6 | Analytics | Lightweight, privacy-friendly; decide before launch | Week 11 |
| A7 | Production VPS provider | Hetzner, smallest orderable plan (some cheap plans are currently unavailable) | Week 12 |

## B. Reference data

| # | Question | Proposal | Needed by |
|---|---|---|---|
| B1 | Cities and districts of Tajikistan | Claude drafts from the official administrative division; owner checks | Week 2 |
| B2 | Specialties with synonyms (ru, tg, transliteration) | Claude drafts for all 12 directions; owner checks | Week 2 |

## C. Product rules

| # | Question | Proposal | Needed by |
|---|---|---|---|
| C1 | Minimum for a master to appear in search | Verified phone + at least 1 specialty + work area; photos optional but rank higher | Week 5 |
| C2 | Master's profile URL from a Cyrillic name | Auto-transliterated slug, editable | Week 5 |
| C3 | Price units in requests and responses | Fixed price or per unit: m², running metre, piece, hour | Week 8 |
| C4 | Open requests that nobody closes | Auto-close after 30 days, reminder 3 days before | Week 8 |
| C5 | Requests stuck in "master chosen" | Ask the client "Is the job done?" after 14 days; close without review after 30 | Week 8 |
| C6 | Review rules | Client can edit for 14 days; master can reply once publicly; master cannot delete | Week 10 |
| C7 | Complaint reasons | Fixed list: spam, fraud, insult, fake photos, other (+ text) | Week 10 |
| C8 | Chat history | Kept while both accounts exist; a deleted user shows as "Deleted user" | Week 10 |
| C9 | Telegram bot texts and tone | Claude drafts ru and tg; owner approves | Week 4 |

## D. Design and brand

| # | Question | Proposal | Needed by |
|---|---|---|---|
| D1 | Style direction, accent color, typeface, ornament | Choose from the same first screen drawn in A, B and C (round 5) | Week 5 |
| D2 | Logo designer: who and budget | Owner picks; paid work needs approval; Claude writes the brief | Week 10 |
| D3 | Final name | Decided together with positioning (round 6) | Week 10 |

## E. Business and legal

| # | Question | Proposal | Needed by |
|---|---|---|---|
| E1 | Lawyer consultation | One meeting covering scraping, privacy policy and terms of use | Week 9 |
| E2 | Privacy policy and terms texts | Claude drafts, lawyer reviews | Week 11 |
| E3 | Registered business (sole proprietor etc.) | Ask the lawyer; required at the latest for payments | Before payments |
| E4 | Support channel | A support Telegram account + email shown in the footer | Week 11 |
| E5 | First masters without scraping | Personal network + surveys with Telegram consent; target 10+ before launch | Week 11 |
| E6 | Where marketing starts | Dushanbe first, the rest of Tajikistan after first traction | Week 12 |
| E7 | Positioning | Round 6 | Week 10 |

## F. Process

| # | Question | Proposal | Needed by |
|---|---|---|---|
| F1 | Weekly click-through | Every Sunday, checklist prepared by Claude | Week 1 |
| F2 | Claude Code setup | `CLAUDE.md` with the brief's rules, reviewer-agent prompt, PR template | Week 1 |

## Decided in this step
- Competitor analysis: skipped by the owner's decision.
- Positioning: moved to round 6.
