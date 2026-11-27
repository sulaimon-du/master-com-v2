# Weeks 8–9 follow-ups (deferred review findings)

Minor findings from the per-task and whole-branch reviews of weeks 8–9 (requests #30, feed #31, worker PR C), taken from the SDD ledger so nothing is lost. None blocks; each was triaged "can wait" by its review.

**For later weeks:** week 10 — `responses_count` and blocked masters with the admin tools (R-026 line 13), the D10.1 "reach the client through chat" line (R-026 line 18), app-wide `RequestValidationError` handler; before week 12 (email provider, A4) — stop queuing email notifications while no provider is configured.

## PR A (Tasks 1–3, `week-8-requests`)
- Task 1: duplicated `_req` helper in the constraint tests.
- Task 1: the migration test leaves `t-mig` rows if killed hard, and would break under parallel test runs.
- Task 2: `_place` N+1 query on the client's request list.
- Task 2: test nits: an unused `db` argument, an inline import, a skipped branch, the inactive-vs-not_found order is implicit.
- Task 3: invalid path ids return FastAPI's JSON 422 (app-wide; add a `RequestValidationError` handler later).
- Task 3: request photo delete is not gated by `uploads_enabled` (matches the portfolio).
- Task 3: budget formatting lives in the template.
- Task 3: the catch-all upload rollback logs the exception type only; 200/422 relies on written == committed.

## PR B (Tasks 4–6, `week-8-feed`)
- Task 4: private helpers used across modules (`_shown_master`, `_specialties`, `_place`, `_pick`): make them public.
- Task 4: `_place` N+1 (feed).
- Task 4: the `hide_other_requests` fixture takes locks in the two-session test.
- Task 4: validation runs before not_found.
- Task 5: the contacts mapping is duplicated (M1).
- Task 6: M7 and M8 of the Task 6 review (deferred at the review).
- PR B whole-branch: M-c — the UX of a master whose own profile is hidden, on the master request views.
- PR B whole-branch: M-d — "My responses" is not paginated.

## PR C (Tasks 7–9, `week-9-worker`)
- Task 7: M6 — a `MATERIALIZED` CTE for the claim query.
- Task 7: M8 — process note: tests written before the code (TDD order).
- Task 8: M2 — a two-session test for the concurrent daily cap.
- Task 8: M3 — an index for the daily-cap query on `jobs` (JSONB fields) and purging `notify` jobs older than 90 days (keep the dedupe keys meanwhile).
- Task 8: M4 — Telegram 400 "chat not found" repeats for every job of that user (consider marking the channel off).
- Task 8: M5 — do not queue email notifications while no email provider is configured (before week 12).
- Task 8: M7 — process note: TDD order.
- Task 9: see the Task 9 review (to be added when it is done).

## Demo seed (separate branch `demo-seed`)
- M2: a total deadline for downloading the photos.
- M6: revisit the job deletes in `demo remove` after PR C (add a real test).
- M7: review counters when reviews arrive (week 10).
- M8: a stricter demo identity check.
