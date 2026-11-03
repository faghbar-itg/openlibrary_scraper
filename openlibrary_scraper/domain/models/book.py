from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BookRecord:
    book_id: str
    source_row: int
    original: dict[str, Any]
    normalized: dict[str, str]
    publication_year: int | None
    valid: bool
    validation_errors: tuple[str, ...] = ()
    duplicate_of: str | None = None

    @property
    def title(self) -> str:
        return self.normalized["book_title"]

    @property
    def author(self) -> str:
        return self.normalized["author"]
