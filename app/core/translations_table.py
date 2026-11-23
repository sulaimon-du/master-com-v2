"""docs/TRANSLATIONS.md: every user-facing text in Russian and Tajik, with the Tajik review status.

The owner reviews Tajik texts in bulk (PRODUCT_RULES C9). A Tajik text is "approved" while it is
exactly the text recorded in app/locales/tg_approved.json; any change makes it a "draft" again.

Regenerate after changing a locale file:  uv run python -m app.core.translations_table
"""

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOCALES = ROOT / "app" / "locales"
TABLE = ROOT / "docs" / "TRANSLATIONS.md"

HEADER = """# Translations (ru / tg)

Generated from `app/locales/ru.json` and `app/locales/tg.json` by
`uv run python -m app.core.translations_table`; do not edit by hand. A test fails when this file
is stale.

**Status:** `approved` = the owner checked this Tajik text; `draft` = written by Claude, to be
checked. To approve, tell Claude which keys are fine (or send corrections); they are recorded in
`app/locales/tg_approved.json`.

"""


@dataclass(frozen=True)
class Row:
    key: str
    ru: str
    tg: str
    status: str


def _load(name: str) -> dict[str, str]:
    return json.loads((LOCALES / name).read_text(encoding="utf-8"))


def rows(ru: dict[str, str], tg: dict[str, str], approved: dict[str, str]) -> list[Row]:
    return [
        Row(
            key,
            ru[key],
            tg.get(key, ""),
            "approved" if key in tg and approved.get(key) == tg[key] else "draft",
        )
        for key in sorted(ru)
    ]


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_rows(table: list[Row]) -> str:
    lines = ["| Key | Русский | Тоҷикӣ | Status |", "|---|---|---|---|"]
    lines += [f"| `{r.key}` | {_cell(r.ru)} | {_cell(r.tg)} | {r.status} |" for r in table]
    return "\n".join(lines)


def render_table() -> str:
    table = rows(_load("ru.json"), _load("tg.json"), _load("tg_approved.json"))
    drafts = sum(r.status == "draft" for r in table)
    summary = f"**{len(table)} texts, {drafts} drafts to check.**\n\n"
    return HEADER + summary + render_rows(table) + "\n"


if __name__ == "__main__":
    TABLE.write_text(render_table(), encoding="utf-8")
    print(f"wrote {TABLE.relative_to(ROOT)}")
