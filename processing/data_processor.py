from __future__ import annotations

import json
import logging
import math
import re
import traceback
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from openpyxl import load_workbook
from rapidfuzz.fuzz import ratio, token_set_ratio

from requests_apis.requests_handler import (
    OpenLibrarySearchClient,
    RetrievalResult,
    Work,
    PaginationService,
    build_search_queries,
    normalized_values,
)
from config.settings import Settings
from processing.storage import save_final_books


_NON_ALPHANUMERIC = re.compile(r"[^\w]+", flags=re.UNICODE)
_SPACE = re.compile(r"\s+")


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


LOGGER = logging.getLogger(__name__)
REQUIRED_COLUMNS = (
    "book_title",
    "author",
    "target_publisher",
    "target_publication_year",
    "target_language_code",
    "subject",
)
OPTIONAL_COLUMNS = ("number_of_pages", "target_number_of_pages")


def _cell_value(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _value_at(row: tuple[Any, ...], position: int) -> Any:
    return _cell_value(row[position] if position < len(row) else None)


def _year(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        numeric = float(value)
        if not numeric.is_integer():
            return None
        year = int(numeric)
    except Exception as exc:
        traceback.print_exc()
        return None
    return year if 0 < year <= 9999 else None


def read_books(path: Path) -> list[BookRecord]:
    LOGGER.info("Reading input workbook: %s", path)
    if not path.is_file():
        raise Exception(f"Input workbook does not exist: {path}")
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        traceback.print_exc()
        raise Exception(f"Could not open input workbook: {path}") from exc

    try:
        books = _read_workbook_records(workbook)
    finally:
        workbook.close()
    valid_count = sum(book.valid for book in books)
    LOGGER.info(
        "Input workbook contains %s records: %s valid, %s invalid.",
        len(books),
        valid_count,
        len(books) - valid_count,
    )
    return books


def _read_workbook_records(workbook: Any) -> list[BookRecord]:
    worksheet = workbook.active
    rows = worksheet.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except Exception as exc:
        traceback.print_exc()
        raise Exception("Input workbook is empty.") from exc

    headers = [normalize_source_text(value).lower() for value in header_row]
    positions = {header: index for index, header in enumerate(headers) if header}
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in positions]
    if missing_columns:
        raise Exception(
            "Input workbook is missing required columns: "
            f"{missing_columns!r}; detected headers={headers!r}."
        )

    books: list[BookRecord] = []
    seen: dict[tuple[str, ...], str] = {}
    for row_number, values in enumerate(rows, start=2):
        record = {
            column: _value_at(values, positions[column])
            for column in REQUIRED_COLUMNS
        }
        record.update(
            {
                column: _value_at(values, positions[column])
                for column in OPTIONAL_COLUMNS
                if column in positions
            }
        )
        if all(value is None or not normalize_source_text(value) for value in record.values()):
            LOGGER.warning("Skipping completely empty input row %s.", row_number)
            continue

        normalized = {key: normalize_source_text(value) for key, value in record.items()}
        normalized["book_title"] = normalize_source_text(record["book_title"])
        errors: list[str] = []
        for required in ("book_title", "author"):
            if not normalized[required]:
                errors.append(f"Missing required value: {required}")
        year = _year(record["target_publication_year"])
        if normalized["target_publication_year"] and year is None:
            errors.append("Invalid target_publication_year")
        key = tuple(normalized[column].casefold() for column in REQUIRED_COLUMNS)
        book_id = f"book_{len(books) + 1:03d}"
        duplicate_of = seen.get(key)
        if duplicate_of:
            errors.append(f"Duplicate input record of {duplicate_of}")
        else:
            seen[key] = book_id
        if "\ufffd" in " ".join(normalized.values()):
            errors.append("Input contains Unicode replacement character U+FFFD")
            LOGGER.warning(
                "Input row %s contains U+FFFD; original value will be preserved.", row_number
            )
        books.append(
            BookRecord(
                book_id=book_id,
                source_row=row_number,
                original=record,
                normalized=normalized,
                publication_year=year,
                valid=not errors,
                validation_errors=tuple(errors),
                duplicate_of=duplicate_of,
            )
        )
    return books


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


WORK_ID = re.compile(r"^OL\d+W$")
YEAR = re.compile(r"\b(?:[1-9]\d{3})\b")
LANGUAGE_NAMES = {
    "ara": "Arabic",
    "chi": "Chinese",
    "zho": "Chinese",
    "dut": "Dutch",
    "nld": "Dutch",
    "eng": "English",
    "fre": "French",
    "fra": "French",
    "ger": "German",
    "deu": "German",
    "heb": "Hebrew",
    "hin": "Hindi",
    "ita": "Italian",
    "jpn": "Japanese",
    "kor": "Korean",
    "per": "Persian",
    "fas": "Persian",
    "por": "Portuguese",
    "rus": "Russian",
    "spa": "Spanish",
    "swe": "Swedish",
    "tur": "Turkish",
    "ukr": "Ukrainian",
}
def extract_work_id(work: Work) -> str | None:
    key = work.raw.get("key")
    if isinstance(key, str):
        path = urlparse(key).path
        match = re.fullmatch(r"/works/(OL\d+W)", path)
        if match:
            return match.group(1)
    if isinstance(work.work_id, str) and WORK_ID.fullmatch(work.work_id):
        return work.work_id
    return None


def edition_id_from_key(key: object) -> str | None:
    if not isinstance(key, str):
        return None
    match = re.fullmatch(r"/books/(OL\d+M)", urlparse(key).path)
    return match.group(1) if match else None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _description(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("value")
    return _text(value)


def _string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return normalized_values(value)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _language_codes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple)):
        values = tuple(
            item.get("key") if isinstance(item, dict) else item
            for item in value
        )
    else:
        return ()
    codes: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        code = urlparse(item).path.rsplit("/", 1)[-1].strip()
        if code:
            codes.append(code)
    return tuple(codes)


def _readable_language(value: object) -> str | None:
    codes = _language_codes(value)
    if not codes:
        return None
    return ", ".join(LANGUAGE_NAMES.get(code.lower(), code) for code in codes)


def _publishers(value: object) -> tuple[str, ...]:
    return _string_values(value)


def _first_publish_date(value: object) -> str | None:
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, str)), None)
    return _text(value)


def _number_of_pages(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        pages = int(value)  # type: ignore[arg-type]
    except Exception as exc:
        traceback.print_exc()
        return None
    return pages if pages > 0 else None


def _target_pages(book: BookRecord) -> int | None:
    for name in ("target_number_of_pages", "number_of_pages"):
        pages = _number_of_pages(book.original.get(name))
        if pages is not None:
            return pages
    return None


def _edition_year(edition: dict[str, Any]) -> int | None:
    value = edition.get("publish_date")
    if not isinstance(value, str):
        return None
    match = YEAR.search(value)
    return int(match.group()) if match else None


def _edition_language_codes(edition: dict[str, Any]) -> tuple[str, ...]:
    return _language_codes(edition.get("languages"))


def _edition_rank(
    edition: dict[str, Any], book: BookRecord
) -> tuple[float, bool]:
    criteria: list[tuple[float, float, bool]] = []

    target_publisher = book.normalized.get("target_publisher", "")
    publishers = _publishers(edition.get("publishers"))
    if target_publisher:
        publisher_scores = [
            token_set_ratio(
                normalize_match_text(target_publisher),
                normalize_match_text(publisher),
            )
            / 100
            for publisher in publishers
        ]
        best_publisher_score = max(publisher_scores, default=0.0)
        criteria.append((0.4, best_publisher_score, best_publisher_score >= 0.6))

    target_year = book.publication_year
    if target_year is not None:
        year_matches = _edition_year(edition) == target_year
        criteria.append((0.25, float(year_matches), year_matches))

    target_language = normalize_match_text(
        book.normalized.get("target_language_code", "")
    )
    if target_language:
        codes = _edition_language_codes(edition)
        language_matches = any(
            normalize_match_text(code) == target_language for code in codes
        )
        criteria.append((0.25, float(language_matches), language_matches))

    target_pages = _target_pages(book)
    edition_pages = _number_of_pages(edition.get("number_of_pages"))
    if target_pages is not None:
        page_match = edition_pages == target_pages
        page_score = (
            1 - abs(edition_pages - target_pages) / max(edition_pages, target_pages)
            if edition_pages is not None
            else 0.0
        )
        criteria.append((0.1, page_score, page_match))

    weight_total = sum(weight for weight, _, _ in criteria)
    if not weight_total:
        return 0.0, False
    score = sum(weight * value for weight, value, _ in criteria) / weight_total
    suitable = any(matched for _, _, matched in criteria)
    return score, suitable


def select_edition(
    editions: list[dict[str, Any]], book: BookRecord
) -> dict[str, Any] | None:
    ranked = []
    for index, edition in enumerate(editions):
        rank = _edition_rank(edition, book)
        edition_id = edition_id_from_key(edition.get("key")) or "unknown-edition-id"
        LOGGER.info(
            "%s Edition %s score=%.3f suitable=%s.",
            book.book_id,
            edition_id,
            rank[0],
            rank[1],
        )
        ranked.append((rank, index, edition))
    suitable = [item for item in ranked if item[0][1]]
    if not suitable:
        return None
    return max(suitable, key=lambda item: (item[0][0], -item[1]))[2]


class OpenLibraryMetadataService:
    def __init__(
        self,
        client: OpenLibrarySearchClient,
        max_pages: int,
    ) -> None:
        self.client = client
        self.max_pages = max_pages

    def retrieve(
        self, book: BookRecord, selected_work: Work | None
    ) -> tuple[dict[str, Any], str, list[str], str | None, str | None]:
        if selected_work is None:
            LOGGER.info("%s has no selected Work; metadata retrieval is skipped.", book.book_id)
            return self.empty_record(), "NO_MATCH", [], None, None

        work_id = extract_work_id(selected_work)
        if work_id is None:
            return (
                self._build_record(book, selected_work, {}, None, None),
                "PARTIAL",
                ["Selected Work has no valid Open Library Work key."],
                None,
                None,
            )

        LOGGER.info("%s retrieving metadata for selected Work %s.", book.book_id, work_id)
        errors: list[str] = []

        try:
            work_payload = self._get_work_details(book.book_id, work_id)
        except Exception as exc:
            traceback.print_exc()
            work_payload = {}
            errors.append(f"Work details: {exc}")

        editions, edition_errors = self._get_editions(book.book_id, work_id)
        errors.extend(f"Editions: {message}" for message in edition_errors)
        selected_edition = select_edition(editions, book)
        selected_edition_id = (
            edition_id_from_key(selected_edition.get("key"))
            if selected_edition
            else None
        )
        edition_url = (
            f"https://openlibrary.org/books/{selected_edition_id}"
            if selected_edition_id
            else None
        )
        record = self._build_record(
            book, selected_work, work_payload, selected_edition, edition_url
        )
        if errors:
            status = "PARTIAL"
        elif editions and selected_edition is None:
            status = "NO_SUITABLE_EDITION"
        elif selected_edition is None:
            status = "NO_EDITION"
        else:
            status = "COMPLETED"

        missing_fields = [field for field, value in record.items() if not value]
        LOGGER.info(
            "%s metadata status=%s; Editions=%s; selected Edition=%s; missing fields=%s.",
            book.book_id,
            status,
            len(editions),
            selected_edition_id or "none",
            ", ".join(missing_fields) if missing_fields else "none",
        )
        return record, status, errors, work_id, selected_edition_id

    @staticmethod
    def empty_record() -> dict[str, Any]:
        return {
            "title": None,
            "author": None,
            "publisher": None,
            "publication_date": None,
            "language": None,
            "number_of_pages": None,
            "subjects": [],
            "description": None,
            "edition_url": None,
            "work_url": None,
        }

    def _get_work_details(self, book_id: str, work_id: str) -> dict[str, Any]:
        LOGGER.info("%s requesting Work details for %s.", book_id, work_id)
        payload = self.client.get_work_details(work_id)
        if not isinstance(payload, dict) or payload.get("error"):
            raise Exception(
                "Open Library returned a malformed Work record: "
                f"book_id={book_id}, work_id={work_id}, payload={payload!r}."
            )
        return payload

    def _get_editions(
        self, book_id: str, work_id: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        editions: list[dict[str, Any]] = []
        errors: list[str] = []
        offset = 0

        for page_index in range(self.max_pages):
            try:
                LOGGER.info(
                    "%s requesting Edition page %s for %s at offset %s.",
                    book_id,
                    page_index + 1,
                    work_id,
                    offset,
                )
                payload = self.client.get_editions_page(work_id, offset)
                if not isinstance(payload, dict):
                    raise Exception(
                        "Open Library Editions response must be a dictionary: "
                        f"book_id={book_id}, work_id={work_id}, payload={payload!r}."
                    )
                entries = payload.get("entries")
                if not isinstance(entries, list) or any(
                    not isinstance(entry, dict) for entry in entries
                ):
                    raise Exception(
                        "Open Library Editions entries parameter must be a list of "
                        f"objects: book_id={book_id}, work_id={work_id}, "
                        f"entries={entries!r}."
                    )
            except Exception as exc:
                traceback.print_exc()
                errors.append(str(exc))
                break

            editions.extend(entries)
            offset += len(entries)
            LOGGER.info(
                "%s Edition page %s returned %s records (total collected: %s).",
                book_id,
                page_index + 1,
                len(entries),
                len(editions),
            )

            try:
                total = int(payload["size"]) if payload.get("size") is not None else None
            except Exception as exc:
                traceback.print_exc()
                total = None
            links = payload.get("links")
            has_next = isinstance(links, dict) and bool(links.get("next"))
            if not entries:
                if total is not None and offset < total:
                    errors.append(
                        "Open Library Editions response ended before its reported total."
                    )
                else:
                    LOGGER.info("%s Edition pagination complete: empty page.", book_id)
                LOGGER.info("%s Edition pagination stopped: empty page.", book_id)
                break
            if total is not None and offset >= total:
                LOGGER.info(
                    "%s Edition pagination complete: collected %s of %s.",
                    book_id,
                    offset,
                    total,
                )
                break
            if (
                not has_next
                and total is None
                and len(entries) < self.client.edition_page_size
            ):
                LOGGER.info(
                    "%s Edition pagination complete after a short final page.",
                    book_id,
                )
                break
        else:
            errors.append(f"Edition pagination reached the limit of {self.max_pages} pages.")

        return editions, errors

    def _build_record(
        self,
        book: BookRecord,
        work: Work,
        work_details: dict[str, Any],
        edition: dict[str, Any] | None,
        edition_url: str | None,
    ) -> dict[str, Any]:
        title = _text(work_details.get("title")) or _text(work.title)
        authors = _string_values(work_details.get("author_name")) or work.authors
        if not authors:
            raw_authors = work_details.get("authors")
            if isinstance(raw_authors, list):
                authors = tuple(
                    name.strip()
                    for item in raw_authors
                    if isinstance(item, dict)
                    for name in (
                        item.get("name")
                        or (
                            item.get("author", {}).get("name")
                            if isinstance(item.get("author"), dict)
                            else None
                        ),
                    )
                    if isinstance(name, str) and name.strip()
                )

        edition_publishers = _publishers(edition.get("publishers")) if edition else ()
        work_publishers = _publishers(work_details.get("publishers")) or work.publishers
        publishers = edition_publishers or work_publishers
        target_publisher = book.normalized.get("target_publisher", "")
        publisher = (
            max(
                publishers,
                key=lambda value: token_set_ratio(
                    normalize_match_text(target_publisher),
                    normalize_match_text(value),
                ),
            )
            if publishers and target_publisher
            else (publishers[0] if publishers else None)
        )

        publication_date = (
            _first_publish_date(edition.get("publish_date"))
            if edition
            else None
        )
        publication_date = (
            publication_date
            or _first_publish_date(work_details.get("first_publish_date"))
            or (str(work.publication_years[0]) if work.publication_years else None)
        )

        language_value = edition.get("languages") if edition else None
        language = _readable_language(language_value)
        if language is None:
            language = _readable_language(work_details.get("languages"))
        if language is None:
            language = _readable_language(work.languages)

        page_count = _number_of_pages(edition.get("number_of_pages")) if edition else None
        if page_count is None:
            page_count = _number_of_pages(work_details.get("number_of_pages"))

        subjects = _string_values(work_details.get("subjects")) or work.subjects
        work_id = extract_work_id(work)
        return {
            "title": title,
            "author": ", ".join(authors) if authors else None,
            "publisher": publisher,
            "publication_date": publication_date,
            "language": language,
            "number_of_pages": page_count,
            "subjects": list(subjects),
            "description": _description(work_details.get("description")),
            "edition_url": edition_url,
            "work_url": f"https://openlibrary.org/works/{work_id}" if work_id else None,
        }


def run_search_pipeline(
    settings: Settings,
    pagination: PaginationService,
    metadata_service: OpenLibraryMetadataService,
    request_counter: Callable[[], int],
    proxy_stats: dict[str, int],
) -> dict[str, Any]:
    books = read_books(settings.input_file)
    LOGGER.info("Starting sequential processing of %s books.", len(books))
    final_books: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    metadata_statuses: Counter[str] = Counter()
    processed = 0
    complete_searches = 0
    incomplete_searches = 0
    total_candidates = 0
    total_pages = 0

    for book_index, book in enumerate(books, start=1):
        LOGGER.info(
            "Starting book %s/%s: %s; title=%r; author=%r.",
            book_index,
            len(books),
            book.book_id,
            book.title,
            book.author,
        )
        if not book.valid:
            message = "; ".join(book.validation_errors)
            LOGGER.error("%s has invalid input: %s", book.book_id, message)
            final_books.append(metadata_service.empty_record())
            metadata_statuses["INVALID_INPUT"] += 1
            errors.append({"book_id": book.book_id, "error": message})
            save_final_books(settings.output_dir, final_books)
            continue

        try:
            queries = build_search_queries(book.title, book.author)
            retrieval = pagination.retrieve(book.book_id, queries)
            scored = [score_work(book, work, settings) for work in retrieval.works]
            total_candidates += len(scored)
            total_pages += retrieval.page_count
            for result in scored:
                LOGGER.info(
                    "%s candidate %s (%r): title=%.3f author=%.3f "
                    "publisher=%.3f year=%.3f language=%.3f subject=%.3f "
                    "total=%.3f status=%s.",
                    book.book_id,
                    result.work.work_id or "unknown-work-id",
                    result.work.title,
                    result.title_score,
                    result.author_score,
                    result.publisher_score,
                    result.year_score,
                    result.language_score,
                    result.subject_score,
                    result.overall_score,
                    result.status,
                )

            chosen, status = select_matches(scored, settings.ambiguity_delta)
            ranked = sorted(
                (match for match in scored if match.work.work_id),
                key=lambda match: match.overall_score,
                reverse=True,
            )
            if (
                len(ranked) > 1
                and ranked[0].overall_score - ranked[1].overall_score
                <= settings.ambiguity_delta
            ):
                if status == "HIGH_CONFIDENCE":
                    status = "POSSIBLE_MATCH"
                LOGGER.info(
                    "%s top Work matches are ambiguous within %.3f score points.",
                    book.book_id,
                    settings.ambiguity_delta,
                )
            LOGGER.info(
                "%s selected Work: %s; match status=%s; score=%.3f.",
                book.book_id,
                chosen.work.work_id if chosen else "none",
                status,
                chosen.overall_score if chosen else 0.0,
            )

            if retrieval.uncertainty:
                warnings.extend(
                    f"{book.book_id}: {warning}" for warning in retrieval.uncertainty
                )
            if not retrieval.complete:
                warnings.append(
                    f"{book.book_id}: search incomplete; selected match may use partial results."
                )
            errors.extend(
                {"book_id": book.book_id, "error": error}
                for error in retrieval.errors
            )

            record, metadata_status, metadata_errors, _, edition_id = (
                metadata_service.retrieve(book, chosen.work if chosen else None)
            )
            final_books.append(record)
            metadata_statuses[metadata_status] += 1
            errors.extend(
                {"book_id": book.book_id, "error": f"Metadata: {error}"}
                for error in metadata_errors
            )
            complete_searches += int(retrieval.complete)
            incomplete_searches += int(not retrieval.complete)
            processed += int(retrieval.complete)
            LOGGER.info(
                "%s finished: %s candidates, %s pages (%s); Edition=%s; "
                "final record ready.",
                book.book_id,
                len(scored),
                retrieval.page_count,
                retrieval.pagination_status,
                edition_id or "none",
            )
        except Exception as exc:
            traceback.print_exc()
            message = f"{type(exc).__name__}: {exc}"
            LOGGER.error("%s failed: %s", book.book_id, message)
            errors.append({"book_id": book.book_id, "error": message})
            metadata_statuses["FAILED"] += 1
            final_books.append(metadata_service.empty_record())

        output_path = save_final_books(settings.output_dir, final_books)
        LOGGER.info(
            "%s final record saved; progress %s/%s; file=%s.",
            book.book_id,
            book_index,
            len(books),
            output_path,
        )

    if not books:
        save_final_books(settings.output_dir, final_books)

    report = {
        "execution_status": "COMPLETED_WITH_ERRORS" if errors else "COMPLETED",
        "total_input_books": len(books),
        "successfully_processed_books": processed,
        "failed_books": len({error["book_id"] for error in errors}),
        "total_openlibrary_requests": request_counter(),
        "total_pages_retrieved": total_pages,
        "total_work_candidates": total_candidates,
        "metadata_records": len(final_books),
        "metadata_status_counts": dict(metadata_statuses),
        "complete_searches": complete_searches,
        "incomplete_searches": incomplete_searches,
        "proxy_statistics": dict(proxy_stats),
        "errors": errors,
        "warnings": list(dict.fromkeys(warnings)),
        "output_file": str(settings.output_dir / "final_books.json"),
    }
    LOGGER.info(
        "Run finished: %s; %s/%s complete searches, %s candidates. "
        "Only final_books.json was saved.",
        report["execution_status"],
        processed,
        len(books),
        total_candidates,
    )
    return report


def run_pipeline(settings: Settings) -> dict[str, Any]:
    with OpenLibrarySearchClient(
        settings.proxy_url,
        settings.user_agent,
        settings.request_timeout,
        settings.max_retries,
        api_base_url=settings.api_base_url,
        edition_page_size=settings.edition_page_size,
    ) as client:
        pagination = PaginationService(
            client,
            settings.page_size,
            settings.max_pages,
        )
        metadata = OpenLibraryMetadataService(client, settings.max_pages)
        return run_search_pipeline(
            settings,
            pagination,
            metadata,
            lambda: client.request_count,
            client.proxy_stats,
        )
