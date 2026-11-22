# Master COM: which model to use in Claude Code

Rule of thumb: **the riskier the code, the stronger the model.** The owner does not review code line by line, so the model that writes the code is the first line of defense; CI and the reviewer agent are the second.

## Defaults

| Who | Model | Why |
|---|---|---|
| Main session (plans and writes code) | **Opus** | Default for everything below unless a row says otherwise |
| `reviewer` agent | **Opus**, pinned in `.claude/agents/reviewer.md` | Its checks must not get weaker when the main model changes |
| `helper` agent (search, run checks, mechanical edits) | **Haiku**, pinned in `.claude/agents/helper.md` | Cheap and fast; never writes logic |
| Fallback when usage limits get tight | `/model opusplan` (Opus plans, Sonnet writes) | Only for tasks marked "Sonnet OK" below |

## By task

| Task | Model | Notes |
|---|---|---|
| Sign-in, sessions, CSRF, OAuth, Telegram login, phone verification | **Opus only** | Weeks 3–4. Subtle bugs here are security bugs |
| Access control, contacts visibility, imported profiles and claiming (R-015, R-016) | **Opus only** | Personal data |
| Uploads and image processing | **Opus only** | Week 5. Untrusted files |
| Data model changes and migrations | **Opus only** | Data loss risk; migrations are hard to undo on production |
| Request lifecycle, chat permissions, reviews, complaints, admin actions | **Opus** | Business rules with access checks |
| Search ranking, SEO pages logic | Opus; Sonnet OK for templates | Week 7 |
| Templates, page layout, CSS, design tokens | Sonnet OK | Only after the view and service code exists |
| Translations ru/tg in translation files | Opus (owner proofreads Tajik) | Quality of language matters more than cost |
| Tests for security-critical code | Same model as the code (Opus) | A weaker model writes weaker tests |
| Searching the codebase, reading logs, summarizing CI failures | Haiku (`helper`) | No code changes |
| Running `ruff`, `pytest`, `alembic check` and reporting results | Haiku (`helper`) | Fixes go back to the main model |
| Renames, formatting reference data files, bumping versions | Haiku (`helper`) | Mechanical only; main model checks the diff |
| Docs and decision log entries | Sonnet OK | |

## Do not

- Do not let Haiku or Sonnet write auth, permissions, uploads or migrations, even with Opus reviewing after. Fix-and-recheck loops cost more than writing it right once, and review catches only part of the mistakes.
- Do not downgrade the `reviewer` agent to save usage. Skip a review never; shorten the diff instead.
- Do not mix models inside one security-critical PR.

## How to switch

- Main session: `/model opus`, `/model sonnet`, `/model opusplan` (check `/model` for the current list).
- Agents: the `model:` line in each file under `.claude/agents/` (`opus`, `sonnet`, `haiku` or `inherit`).
- Reference: https://docs.anthropic.com/en/docs/claude-code/model-config
