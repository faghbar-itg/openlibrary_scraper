from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
