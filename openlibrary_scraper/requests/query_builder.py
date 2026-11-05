from __future__ import annotations

from typing import Any


SEARCH_FIELDS = "key,title,author_name,publisher,publish_year,language,subject"


def build_search_queries(title: str, author: str) -> tuple[str, ...]:
    def quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').strip()

    title = quote(title)
    if not title:
        raise ValueError("A title is required to build an Open Library search.")
    author = quote(author)
    primary = f'title:"{title}" AND author:"{author}"' if author else f'title:"{title}"'
    fallback = f'title:"{title}"'
    return (primary,) if primary == fallback else (primary, fallback)


def prepare_search_params(query: str, limit: int, offset: int) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("A search query is required.")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("Search page limit must be a positive integer.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("Search page offset must be a non-negative integer.")

    return {
        "q": query,
        "fields": SEARCH_FIELDS,
        "limit": limit,
        "offset": offset,
    }
