from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openlibrary_scraper.domain.models.work import Work


@dataclass(frozen=True)
class SearchPage:
    num_found: int | None
    num_found_exact: bool | None
    start: int | None
    docs: tuple[Work, ...]
    raw: dict[str, Any]
