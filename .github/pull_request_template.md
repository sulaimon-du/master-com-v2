## What and why
<!-- One or two sentences. Link the brief section or decision (R-xxx). -->

## How to check it
<!-- Steps the owner can click through on staging. -->

## Checklist
- [ ] Matches `docs/BRIEF.md`; any deviation is logged in `docs/DECISIONS.md`
- [ ] Tests added or updated for new behavior; access control tested for every new write
- [ ] No secrets, no `|safe` on user data, no `x-html`, no inline scripts or styles
- [ ] Migrations included and reversible; `alembic check` passes
- [ ] Russian and Tajik texts go through translation files, not hard-coded
- [ ] The `reviewer` agent ran on this PR and its blocking findings are fixed
