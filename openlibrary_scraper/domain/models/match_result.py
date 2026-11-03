from __future__ import annotations

from dataclasses import dataclass

from openlibrary_scraper.domain.models.work import Work


@dataclass(frozen=True)
class MatchResult:
    work: Work
    title_score: float
    author_score: float
    publisher_score: float
    year_score: float
    language_score: float
    subject_score: float
    overall_score: float
    status: str
    reason: str
