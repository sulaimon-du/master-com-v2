"""Translations: Russian (default) and Tajik. Catalogs are JSON files in app/locales/.

Every key must exist in both files; tests enforce it. A missing Tajik key falls back to Russian.
"""

import json
from functools import lru_cache
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
LANGS = ("ru", "tg")
DEFAULT_LANG = "ru"


@lru_cache
def _catalog(lang: str) -> dict[str, str]:
    return json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def translate(lang: str, key: str, **params: str) -> str:
    text = _catalog(lang).get(key) or _catalog(DEFAULT_LANG).get(key) or key
    return text.format(**params) if params else text


def normalize_lang(value: str | None) -> str:
    return value if value in LANGS else DEFAULT_LANG
