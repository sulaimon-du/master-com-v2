# Week 7 follow-ups (deferred review findings)

Minor findings from the per-task and whole-branch reviews of week 7 (search #28, SEO PR B). None blocks; triaged "can wait" by the final reviews.

**For later weeks:** week 8 — "post a request" on every SEO page and price range from requests; week 9 — rating updates must call refresh_search; week 10 — admin hide/unblock/delete and photo moderation must call refresh_search, deactivated specialties on cards; production launch — SITE_URL https + APP_ENV=production, check /robots.txt and /sitemap.xml by hand.

- Task 1: minor (deferred): vacuous assert place_synonyms_added >= 0; per-row stale delete; untyped dicts in seed; no Latin for hisor/roghun; admin-added place synonyms wiped by seed (week 10); no test that GIN/trgm/partial indexes exist
- Task 2: minor (deferred): concurrent edit+block can leave is_visible=true briefly (mitigated: search re-checks users at query time; rebuild heals); first-row insert PK race → 500 (use ON CONFLICT); weak tests (cap, orphan, tg name); duplicated active check
- Task 3: minor (deferred): savepoint rollback can mask a non-cancel OperationalError; counts_by_district_specialty(named_only=False) nested loop + no City.is_active check; test asserts statement_timeout == "0" (environment-dependent); duplicated ru/tg pick; search.py ~700 lines
- Task 4: minor (deferred): tg home test asserts only absence of ru; no hostile-q escaping regression test; district select needs reload (no-JS, owner informed); weak "search.remove" assertion
- Task 5: minor (deferred): timed-out SEO page meta says 0 masters + raw page in canonical; vacuous robots assertion; weak district title test; empty-page CTA href="/" on /tg (week 8); trailing-slash 307 redirect untested
- Task 6: minor (deferred): 500 response bypasses security headers (pure-ASGI wrapper later); hidden-profile owner view says index,follow (only owner sees it); loose hreflang assertion; title fallbacks lightly tested
- Task 7: minor (deferred): no escaping regression test; loose guard-log assertion; misnamed robots/sitemap test; only URL-count guard (no 50 MB check); 10-min stale window for sitemap vs live meta (documented)
