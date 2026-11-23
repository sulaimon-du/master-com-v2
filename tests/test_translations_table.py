from pathlib import Path

from app.core import translations_table as tt

ROOT = Path(__file__).resolve().parent.parent


def test_table_file_matches_the_locales() -> None:
    # Regenerate with: uv run python -m app.core.translations_table
    current = (ROOT / "docs" / "TRANSLATIONS.md").read_text(encoding="utf-8")
    assert current == tt.render_table(), "docs/TRANSLATIONS.md is stale; regenerate it"


def test_status_is_approved_only_while_the_tajik_text_is_unchanged() -> None:
    ru = {"a": "Привет", "b": "Пока", "c": "Да"}
    tg = {"a": "Салом", "b": "Хайр (нав)", "c": "Ҳа"}
    approved = {"a": "Салом", "b": "Хайр"}
    rows = tt.rows(ru, tg, approved)
    assert [(r.key, r.status) for r in rows] == [
        ("a", "approved"),
        ("b", "draft"),
        ("c", "draft"),
    ]


def test_pipes_and_newlines_cannot_break_the_table() -> None:
    rows = tt.rows({"k": "a | b\nc"}, {"k": "x|y"}, {})
    line = tt.render_rows(rows).splitlines()[-1]
    assert line.count(" | ") == 3
    assert "\\|" in line
