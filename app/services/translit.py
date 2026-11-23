# ruff: noqa: RUF001  (the transliteration table must contain look-alike Cyrillic letters)
"""Cyrillic (Russian and Tajik) to Latin, shared by slugs and the search index."""

import re

_RU = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}  # fmt: skip
_TG = {"ғ": "gh", "ӣ": "i", "қ": "q", "ӯ": "u", "ҳ": "h", "ҷ": "j"}
_TRANSLIT = {**_RU, **_TG}


def translit(text: str) -> str:
    """Lowercase Latin text: letters and digits kept, anything else becomes one space."""
    latin = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
    return re.sub(r"[^a-z0-9]+", " ", latin).strip()
