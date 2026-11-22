# Weeks 5–6 follow-ups (deferred review findings)

Minor findings from the per-task and whole-branch reviews of PR A (#22) and PR B. None blocks; triaged as "can wait" by the final reviews. Pick up in a small polish PR or when the area is next touched.

**Before week 7 search:** list only published profiles whose owner is active (published_at stays set on a blocked owner).
**Before real traffic:** purge job for soft-deleted photos older than 30 days and orphan files; move the Pillow DecompressionBomb warnings filter to module level; convert ICC profiles to sRGB.

- Task 1: minor (deferred): KEY_RE \d matches Unicode digits (use [0-9] or re.ASCII); media route builds LocalDiskStorage directly; mkstemp 0600 mode; no fsync; failure-cleanup branch untested
- Task 2: minor (deferred): warnings.catch_warnings per call is not thread-safe (move filter to module level); no 40–80 MP bomb test; tRNS transparency on L/RGB not composited; ICC dropped without sRGB conversion; no ICC input test; duplicated try/except
- Task 3: minor (deferred): redundant `and user`; schema↔service import cycle (move BasicsInput to schemas); collections loosely typed until Task 9; inactive specialty blocks re-save (edit form must not pre-tick inactive); test phone generator could collide
- Task 4: minor (deferred): service basics error codes map to errors["form"] which no template renders (silent 422 if BasicsForm drifts); contact errors shown twice; slug-check redirects guests (HTMX swaps sign-in page); _page loads catalog/cities for every page; duplicated locale strings; misnamed test test_ru_error_keys_exist_in_both_locales; ORM user in phone-step template
- Task 4: minor (deferred): chunked/lying body gets 400 not 413 on FastAPI routes; body_limit except Exception swallows app errors after cap; 413 skips security headers; uploads-disabled body-limit test does not exercise the setting. Task 8 must add photo upload prefixes to UPLOAD_PATH_PREFIXES (multi-file cap).
- Task 5: minor (deferred): no blocked-viewer test / owner email absence assert; wa_digits implicit; FIX_LINKS hardcoded paths; date format not locale-aware
- Task 6: minor (deferred): Pillow cutoff date not in R-022; dependency reason not its own line; MEDIA_ROOT relative path not stated; README /media "(when uploads are on)"
- Task 7: minor (deferred): PhotoView.deleted always False; collection create count/position unlocked (31 possible under a race); storage I/O under collection lock (matters with R2); storage injection inconsistent; _cards N+1 queries; concurrency test loses non-PortfolioError exceptions and leaves files; delete/move photo take no collection lock
- Task 8: minor (deferred): MULTI_UPLOAD_PATHS regex \d/$ (use [0-9]+ and fullmatch; DEFAULT_CAP when uploads off); orphan files if commit fails after add_photo; step-5 partial failure renders on POST (refresh re-creates collection; use PRG); finishing mode lost after retry upload; unused max_files + hard-coded "10" in locales; imports private helpers from master.py; _card builds all cards; undo tile None → 303 into <li>; type: ignore in portfolio_upload
- Task 9: minor (deferred): profile_view computes public_collections on the collection page (unused); thumbnail alt is only a number; owner sees moderation-hidden photo unmarked; collection 404 no-store not asserted; JS counter rounding while scrolling
