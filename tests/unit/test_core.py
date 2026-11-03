from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from openlibrary_scraper.application.pagination_service import PaginationService
from openlibrary_scraper.config.settings import Settings, load_settings
from openlibrary_scraper.domain.models.book import BookRecord
from openlibrary_scraper.domain.models.search_result import SearchPage
from openlibrary_scraper.domain.models.work import Work
from openlibrary_scraper.domain.services.query_builder import build_search_queries
from openlibrary_scraper.domain.services.scoring_service import score_work
from openlibrary_scraper.domain.services.text_normalizer import normalize_match_text
from openlibrary_scraper.infrastructure.openlibrary.response_parser import (
    parse_search_page,
)
from openlibrary_scraper.infrastructure.storage.checkpoint_store import CheckpointStore
from openlibrary_scraper.infrastructure.storage.raw_response_store import RawResponseStore
from openlibrary_scraper.processing.input_reader import read_books
from openlibrary_scraper.utils.errors import (
    ConfigurationError,
    InputValidationError,
    SearchError,
)


ROOT = Path(__file__).resolve().parents[2]


def _book() -> BookRecord:
    return BookRecord(
        book_id="book_001",
        source_row=2,
        original={
            "book_title": 'A "Quoted" Tale',
            "author": "O'Neil",
            "target_publisher": "Sample Press",
            "target_publication_year": 2020,
            "target_language_code": "eng",
            "subject": "Fiction",
        },
        normalized={
            "book_title": 'A "Quoted" Tale',
            "author": "O'Neil",
            "target_publisher": "Sample Press",
            "target_publication_year": "2020",
            "target_language_code": "eng",
            "subject": "Fiction",
        },
        publication_year=2020,
        valid=True,
    )


def _work(key: str, title: str = "Sample Book") -> Work:
    return Work(
        work_id=key,
        work_url=f"https://openlibrary.org/works/{key}" if key else None,
        title=title,
        authors=("Sample Author",),
        publishers=("Sample Press",),
        publication_years=(2020,),
        languages=("eng",),
        subjects=("Fiction",),
        raw={"key": f"/works/{key}" if key else None, "title": title},
    )


def test_normalization_and_query_escaping() -> None:
    assert normalize_match_text("  Café—Books   & Stories ") == "café books stories"
    primary, fallback = build_search_queries(_book())
    assert primary == 'title:"A \\"Quoted\\" Tale" AND author:"O\'Neil"'
    assert fallback == 'title:"A \\"Quoted\\" Tale"'


def test_input_reader_preserves_all_rows_and_unicode_author() -> None:
    books = read_books(ROOT / "data" / "input" / "books.xlsx")
    assert len(books) == 20
    numeric_title = next(book for book in books if book.source_row == 4)
    assert numeric_title.original["book_title"] == 1984
    assert numeric_title.title == "1984"
    damaged_author = next(book for book in books if book.source_row == 10)
    assert "é" in damaged_author.original["author"]
    assert not any("U+FFFD" in error for error in damaged_author.validation_errors)


def test_input_validation_reports_missing_headers_and_duplicates(tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["book_title"])
    worksheet.append(["Only a title"])
    workbook.save(invalid_path)
    with pytest.raises(InputValidationError, match="Missing required columns"):
        read_books(invalid_path)

    duplicate_path = tmp_path / "duplicates.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        [
            "book_title",
            "author",
            "target_publisher",
            "target_publication_year",
            "target_language_code",
            "subject",
        ]
    )
    row = ["The Hobbit", "J. R. R. Tolkien", "Houghton Mifflin", 2001, "eng", "Fantasy"]
    worksheet.append(row)
    worksheet.append(row)
    workbook.save(duplicate_path)
    books = read_books(duplicate_path)
    assert len(books) == 2
    assert books[0].valid
    assert not books[1].valid
    assert books[1].duplicate_of == books[0].book_id


def test_search_response_parser_handles_missing_fields_and_rejects_bad_shape() -> None:
    page = parse_search_page(
        {"docs": [{"key": "/works/OL123W"}, {"key": "/editions/OL5M"}]}
    )
    assert page.num_found is None
    assert page.docs[0].title == ""
    assert page.docs[0].work_id == "OL123W"
    assert page.docs[1].work_id is None
    with pytest.raises(SearchError):
        parse_search_page({"docs": {}})


def test_load_settings_identifies_all_missing_node4_variables(monkeypatch) -> None:
    monkeypatch.setattr("openlibrary_scraper.config.settings.load_dotenv", lambda _: None)
    for name in ("NODE4_HOST", "NODE4_PORT", "NODE4_USERNAME", "NODE4_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigurationError) as error:
        load_settings()

    assert all(
        name in str(error.value)
        for name in ("NODE4_HOST", "NODE4_PORT", "NODE4_USERNAME", "NODE4_PASSWORD")
    )


def test_load_settings_url_encodes_node4_credentials(monkeypatch) -> None:
    monkeypatch.setattr("openlibrary_scraper.config.settings.load_dotenv", lambda _: None)
    for name, value in {
        "NODE4_HOST": "proxy.example",
        "NODE4_PORT": "8080",
        "NODE4_USERNAME": "name@domain",
        "NODE4_PASSWORD": "s:p /#",
    }.items():
        monkeypatch.setenv(name, value)

    settings = load_settings()

    assert (
        settings.proxy_url
        == "http://name%40domain:s%3Ap%20%2F%23@proxy.example:8080"
    )


def test_scoring_uses_agreed_weights_and_keeps_subject_out_of_total() -> None:
    book = _book()
    exact_work = Work(
        work_id="OL1W",
        work_url="https://openlibrary.org/works/OL1W",
        title=book.title,
        authors=(book.author,),
        publishers=("Sample Press",),
        publication_years=(2020,),
        languages=("eng",),
        subjects=("Unrelated subject",),
        raw={},
    )
    settings = Settings(
        project_root=ROOT,
        proxy_url="http://user:pass@proxy.example:8080",
        request_timeout=1,
        max_retries=0,
        page_size=100,
        max_pages=10,
        resume_enabled=True,
        start_fresh=False,
        log_level="INFO",
        user_agent="test",
        input_file=ROOT / "input.xlsx",
        output_dir=ROOT / "output",
        high_confidence_threshold=0.85,
        possible_match_threshold=0.60,
        ambiguity_delta=0.03,
    )
    scored = score_work(book, exact_work, settings)
    assert scored.overall_score == pytest.approx(1.0)
    assert scored.subject_score > 0.0
    assert scored.status == "HIGH_CONFIDENCE"


class _PageClient:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, int, int]] = []

    def search_page(self, query: str, limit: int, offset: int) -> SearchPage:
        self.calls.append((query, limit, offset))
        page_index = len(self.calls) - 1
        return parse_search_page(self.pages[page_index])


def _page(start: int, docs: list[dict], total: int = 3) -> dict:
    return {
        "numFound": total,
        "numFoundExact": True,
        "start": start,
        "docs": docs,
    }


def test_pagination_deduplicates_work_ids_and_resumes_saved_pages(tmp_path: Path) -> None:
    pages = [
        _page(0, [{"key": "/works/OL1W"}, {"key": "/works/OL2W"}]),
        _page(2, [{"key": "/works/OL2W"}, {"key": "/works/OL3W"}]),
    ]
    client = _PageClient(pages)
    checkpoints = CheckpointStore(tmp_path)
    checkpoints.initialize(resume=False, fresh=True)
    raw_store = RawResponseStore(tmp_path)
    first = PaginationService(client, raw_store, checkpoints, page_size=2, max_pages=1)
    partial = first.retrieve("book_001", ('title:"Book"',))
    assert not partial.complete
    assert len(client.calls) == 1

    resumed = PaginationService(client, raw_store, checkpoints, page_size=2, max_pages=3)
    complete = resumed.retrieve("book_001", ('title:"Book"',))
    assert complete.complete
    assert complete.retrieved_total == 4
    assert len(complete.works) == 3
    assert len(client.calls) == 2
    assert [call[2] for call in client.calls] == [0, 2]


def test_fallback_query_raw_pages_do_not_overwrite_primary(tmp_path: Path) -> None:
    client = _PageClient([
        _page(0, [], 0),
        _page(0, [{"key": "/works/OL9W"}], 1),
    ])
    checkpoints = CheckpointStore(tmp_path)
    checkpoints.initialize(resume=False, fresh=True)
    service = PaginationService(
        client, RawResponseStore(tmp_path), checkpoints, page_size=10, max_pages=2
    )
    result = service.retrieve(
        "book_001", ('title:"Book" AND author:"Author"', 'title:"Book"')
    )
    assert result.complete
    assert result.query == 'title:"Book"'
    assert result.works[0].work_id == "OL9W"
    assert len(client.calls) == 2


def test_inconsistent_response_start_is_not_reported_complete(tmp_path: Path) -> None:
    client = _PageClient([_page(1, [{"key": "/works/OL1W"}], 1)])
    checkpoints = CheckpointStore(tmp_path)
    checkpoints.initialize(resume=False, fresh=True)
    service = PaginationService(
        client, RawResponseStore(tmp_path), checkpoints, page_size=10, max_pages=2
    )
    result = service.retrieve("book_001", ('title:"Book"',))
    assert not result.complete
    assert result.pagination_status == "INCOMPLETE"
    assert any("response start" in message for message in result.uncertainty)
