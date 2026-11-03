from __future__ import annotations

from rapidfuzz.fuzz import ratio, token_set_ratio

from openlibrary_scraper.config.settings import Settings
from openlibrary_scraper.domain.models.book import BookRecord
from openlibrary_scraper.domain.models.match_result import MatchResult
from openlibrary_scraper.domain.models.work import Work
from openlibrary_scraper.domain.services.text_normalizer import normalize_match_text


WEIGHTS = {
    "title": 0.35,
    "author": 0.30,
    "publisher": 0.20,
    "year": 0.10,
    "language": 0.05,
}


def _best_text_score(target: str, candidates: tuple[str, ...]) -> float:
    normalized_target = normalize_match_text(target)
    if not normalized_target or not candidates:
        return 0.0
    return max(
        max(
            ratio(normalized_target, normalize_match_text(candidate)),
            token_set_ratio(normalized_target, normalize_match_text(candidate)),
        )
        for candidate in candidates
    ) / 100


def score_work(book: BookRecord, work: Work, settings: Settings) -> MatchResult:
    title_score = _best_text_score(book.title, (work.title,))
    author_score = _best_text_score(book.author, work.authors)
    publisher_score = _best_text_score(book.normalized["target_publisher"], work.publishers)
    year_score = (
        float(book.publication_year in work.publication_years)
        if book.publication_year is not None and work.publication_years
        else 0.0
    )
    target_language = normalize_match_text(book.normalized["target_language_code"])
    language_score = (
        float(any(target_language == normalize_match_text(code) for code in work.languages))
        if target_language and work.languages
        else 0.0
    )
    subject_score = _best_text_score(book.normalized["subject"], work.subjects)
    overall = (
        title_score * WEIGHTS["title"]
        + author_score * WEIGHTS["author"]
        + publisher_score * WEIGHTS["publisher"]
        + year_score * WEIGHTS["year"]
        + language_score * WEIGHTS["language"]
    )

    if work.work_id is None:
        status = "LOW_CONFIDENCE"
    elif overall >= settings.high_confidence_threshold:
        status = "HIGH_CONFIDENCE"
    elif overall >= settings.possible_match_threshold:
        status = "POSSIBLE_MATCH"
    else:
        status = "LOW_CONFIDENCE"

    reason = (
        f"title={title_score:.3f}, author={author_score:.3f}, "
        f"publisher={publisher_score:.3f}, year={year_score:.3f}, "
        f"language={language_score:.3f}; subject={subject_score:.3f} "
        "(supporting signal only; Search API edition-level fields are not edition confirmation)"
    )
    return MatchResult(
        work, title_score, author_score, publisher_score, year_score,
        language_score, subject_score, overall, status, reason,
    )


def select_matches(
    results: list[MatchResult], ambiguity_delta: float
) -> tuple[MatchResult | None, str]:
    valid = sorted(
        (result for result in results if result.work.work_id),
        key=lambda result: result.overall_score,
        reverse=True,
    )
    if not valid:
        return None, "NO_MATCH"
    best = valid[0]
    if len(valid) > 1 and best.overall_score - valid[1].overall_score <= ambiguity_delta:
        return best, best.status
    if best.status == "LOW_CONFIDENCE":
        return None, "NO_MATCH"
    return best, best.status
