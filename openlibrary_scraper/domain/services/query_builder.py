from __future__ import annotations

from openlibrary_scraper.domain.models.book import BookRecord


def _quote_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').strip()


def build_search_queries(book: BookRecord) -> tuple[str, ...]:
    title = _quote_query_value(book.title)
    if not title:
        raise ValueError(f"Book {book.book_id} has no title for search.")
    author = _quote_query_value(book.author)
    primary = f'title:"{title}" AND author:"{author}"' if author else f'title:"{title}"'
    fallback = f'title:"{title}"'
    return (primary,) if primary == fallback else (primary, fallback)
