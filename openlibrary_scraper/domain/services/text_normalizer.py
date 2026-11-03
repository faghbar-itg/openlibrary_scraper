from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


_NON_ALPHANUMERIC = re.compile(r"[^\w]+", flags=re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize_source_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = unicodedata.normalize("NFKC", str(value))
    return _SPACE.sub(" ", text).strip()


def normalize_match_text(value: object) -> str:
    text = normalize_source_text(value).casefold()
    text = _NON_ALPHANUMERIC.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def normalized_values(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,) if values else ()
    if isinstance(values, Iterable):
        return tuple(str(value) for value in values if value is not None and str(value).strip())
    return ()
