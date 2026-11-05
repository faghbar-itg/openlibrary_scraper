from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from openlibrary_scraper.errors import SearchError


_WORK_KEY = re.compile(r"^/works/(OL\d+W)$")


@dataclass(frozen=True)
class Work:
    work_id: str | None
    work_url: str | None
    title: str
    authors: tuple[str, ...]
    publishers: tuple[str, ...]
    publication_years: tuple[int, ...]
    languages: tuple[str, ...]
    subjects: tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class SearchPage:
    num_found: int | None
    num_found_exact: bool | None
    start: int | None
    docs: tuple[Work, ...]
    raw: dict[str, Any]


def normalized_values(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,) if values else ()
    if isinstance(values, Iterable):
        return tuple(
            str(value)
            for value in values
            if value is not None and str(value).strip()
        )
    return ()


def _years(values: object) -> tuple[int, ...]:
    results: set[int] = set()
    for value in normalized_values(values):
        try:
            year = int(value)
        except ValueError:
            continue
        if 0 < year <= 9999:
            results.add(year)
    return tuple(sorted(results))


def parse_work(payload: object) -> Work:
    if not isinstance(payload, dict):
        raise SearchError("Open Library returned a non-object work document.")
    key = payload.get("key")
    match = _WORK_KEY.fullmatch(key) if isinstance(key, str) else None
    work_id = match.group(1) if match else None
    return Work(
        work_id=work_id,
        work_url=f"https://openlibrary.org/works/{work_id}" if work_id else None,
        title=next(iter(normalized_values(payload.get("title"))), ""),
        authors=normalized_values(payload.get("author_name")),
        publishers=normalized_values(payload.get("publisher")),
        publication_years=_years(payload.get("publish_year")),
        languages=normalized_values(payload.get("language")),
        subjects=normalized_values(payload.get("subject")),
        raw=payload,
    )


def parse_search_page(payload: object) -> SearchPage:
    if not isinstance(payload, dict):
        raise SearchError("Open Library response must be a JSON object.")
    docs = payload.get("docs")
    if not isinstance(docs, list):
        raise SearchError("Open Library response is missing its docs list.")

    num_found_value = payload.get("numFound", payload.get("num_found"))
    try:
        num_found = int(num_found_value) if num_found_value is not None else None
    except (TypeError, ValueError):
        num_found = None
    exact_value = payload.get("numFoundExact")
    num_found_exact = exact_value if isinstance(exact_value, bool) else None
    start_value = payload.get("start")
    try:
        start = int(start_value) if start_value is not None else None
    except (TypeError, ValueError):
        start = None
    works = tuple(parse_work(doc) for doc in docs)
    return SearchPage(num_found, num_found_exact, start, works, payload)
