from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from rapidfuzz.fuzz import token_set_ratio

from openlibrary_scraper.processing.books import (
    BookRecord,
    normalize_match_text,
)
from openlibrary_scraper.requests.response_parser import Work, normalized_values
from openlibrary_scraper.requests.client import OpenLibrarySearchClient
from openlibrary_scraper.processing.storage import CheckpointStore, RawResponseStore
from openlibrary_scraper.errors import SearchError


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
    except (TypeError, ValueError):
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
    ranked = [
        (_edition_rank(edition, book), index, edition)
        for index, edition in enumerate(editions)
    ]
    suitable = [item for item in ranked if item[0][1]]
    if not suitable:
        return None
    return max(suitable, key=lambda item: (item[0][0], -item[1]))[2]


class OpenLibraryMetadataService:
    def __init__(
        self,
        client: OpenLibrarySearchClient,
        raw_store: RawResponseStore,
        checkpoints: CheckpointStore,
        max_pages: int,
    ) -> None:
        self.client = client
        self.raw_store = raw_store
        self.checkpoints = checkpoints
        self.max_pages = max_pages

    def retrieve(
        self, book: BookRecord, selected_work: Work | None
    ) -> tuple[dict[str, Any], str, list[str], str | None, str | None]:
        if selected_work is None:
            state = self.checkpoints.book_state(book.book_id).setdefault("metadata", {})
            state.update({"status": "NO_MATCH", "errors": []})
            self.checkpoints.save()
            return self.empty_record(), "NO_MATCH", [], None, None

        work_id = extract_work_id(selected_work)
        if work_id is None:
            state = self.checkpoints.book_state(book.book_id).setdefault("metadata", {})
            state.update(
                {
                    "status": "PARTIAL",
                    "errors": ["Selected Work has no valid Open Library Work key."],
                }
            )
            self.checkpoints.save()
            return (
                self._build_record(book, selected_work, {}, None, None),
                "PARTIAL",
                ["Selected Work has no valid Open Library Work key."],
                None,
                None,
            )

        state = self.checkpoints.book_state(book.book_id).setdefault("metadata", {})
        if state.get("work_id") != work_id:
            state.clear()
            state["work_id"] = work_id
        state["work_url"] = f"https://openlibrary.org/works/{work_id}"
        errors: list[str] = []

        try:
            work_payload = self._get_work_details(book.book_id, work_id, state)
        except SearchError as exc:
            work_payload = {}
            errors.append(f"Work details: {exc}")

        editions, edition_errors = self._get_editions(book.book_id, work_id, state)
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

        state.update(
            {
                "status": status,
                "edition_id": selected_edition_id,
                "edition_url": edition_url,
                "errors": errors,
                "final_record": record,
            }
        )
        self.checkpoints.save()
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

    def _get_work_details(
        self, book_id: str, work_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        payload = self.raw_store.load_work_details(
            self.checkpoints.run_id, book_id, work_id
        )
        if payload is None:
            response = self.client.get_work_details(work_id)
            if not isinstance(response, dict) or response.get("error"):
                raise SearchError("Open Library returned a malformed Work record.")
            payload = response
            self.raw_store.save_work_details(
                self.checkpoints.run_id, book_id, work_id, payload
            )
            state["work_details_saved"] = True
            self.checkpoints.save()
        return payload

    def _get_editions(
        self, book_id: str, work_id: str, state: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        editions: list[dict[str, Any]] = []
        errors: list[str] = []
        offset = 0
        edition_state = state.setdefault("editions", {})

        for page_index in range(self.max_pages):
            try:
                payload = self.raw_store.load_editions_page(
                    self.checkpoints.run_id, book_id, work_id, page_index
                )
                if payload is None:
                    response = self.client.get_editions_page(work_id, offset)
                    if not isinstance(response, dict):
                        raise SearchError("Open Library returned a malformed Editions response.")
                    payload = response
                    self.raw_store.save_editions_page(
                        self.checkpoints.run_id,
                        book_id,
                        work_id,
                        page_index,
                        payload,
                    )
                entries = payload.get("entries")
                if not isinstance(entries, list) or any(
                    not isinstance(entry, dict) for entry in entries
                ):
                    raise SearchError(
                        "Open Library Editions response is missing a valid entries list."
                    )
            except SearchError as exc:
                errors.append(str(exc))
                edition_state["complete"] = False
                break

            editions.extend(entries)
            offset += len(entries)
            edition_state["pages"] = page_index + 1
            self.checkpoints.save()

            try:
                total = int(payload["size"]) if payload.get("size") is not None else None
            except (TypeError, ValueError):
                total = None
            links = payload.get("links")
            has_next = isinstance(links, dict) and bool(links.get("next"))
            if not entries:
                if total is not None and offset < total:
                    errors.append(
                        "Open Library Editions response ended before its reported total."
                    )
                    edition_state["complete"] = False
                else:
                    edition_state["complete"] = True
                break
            if total is not None and offset >= total:
                edition_state["complete"] = True
                break
            if (
                not has_next
                and total is None
                and len(entries) < self.client.edition_page_size
            ):
                edition_state["complete"] = True
                break
        else:
            errors.append(f"Edition pagination reached the limit of {self.max_pages} pages.")
            edition_state["complete"] = False

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
