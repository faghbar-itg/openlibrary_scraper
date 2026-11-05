from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests
from openpyxl import Workbook, load_workbook

from openlibrary_scraper.requests.pagination import PaginationService
from openlibrary_scraper.processing.metadata import OpenLibraryMetadataService
from openlibrary_scraper.processing.pipeline import SearchPipeline
from openlibrary_scraper.config.settings import Settings
from openlibrary_scraper.requests.response_parser import SearchPage, parse_search_page
from openlibrary_scraper.requests.client import (
    OpenLibrarySearchClient,
    editions_url,
    work_details_url,
)
from openlibrary_scraper.requests.query_builder import prepare_search_params
from openlibrary_scraper.processing.storage import CheckpointStore, RawResponseStore
from openlibrary_scraper.errors import SearchError


class _Response:
    def __init__(self, status_code: int, payload: Any, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}

    def json(self) -> Any:
        return self.payload


class _SearchSession:
    def __init__(self, responses: list[_Response] | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self.responses = responses or [
            _Response(429, {}, {"Retry-After": "0"}),
            _Response(200, {"numFound": 0, "numFoundExact": True, "start": 0, "docs": []}),
        ]

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


def test_search_client_reuses_proxy_for_http_and_https(monkeypatch) -> None:
    monkeypatch.setattr(
        "openlibrary_scraper.requests.client.time.sleep",
        lambda _: None,
    )
    proxy_url = "http://encoded-user:encoded-password@proxy.example:8080"
    session = _SearchSession()
    client = OpenLibrarySearchClient(
        proxy_url, "test-agent", 3, 1, session=session  # type: ignore[arg-type]
    )

    result = client.search_page('title:"A Book"', 10, 0)

    expected = {"http": proxy_url, "https": proxy_url}
    assert isinstance(result, SearchPage)
    assert len(session.calls) == 2
    assert all(call["proxies"] == expected for call in session.calls)
    assert session.calls[0]["params"] == {
        "q": 'title:"A Book"',
        "fields": "key,title,author_name,publisher,publish_year,language,subject",
        "limit": 10,
        "offset": 0,
    }


@pytest.mark.parametrize(
    ("query", "limit", "offset"),
    [
        ("", 10, 0),
        ("title:test", 0, 0),
        ("title:test", 10, -1),
        ("title:test", True, 0),
        ("title:test", 10, False),
    ],
)
def test_prepare_search_params_rejects_invalid_values(
    query: str, limit: int, offset: int
) -> None:
    with pytest.raises(ValueError):
        prepare_search_params(query, limit, offset)


def test_search_client_reports_proxy_authentication_failure() -> None:
    client = OpenLibrarySearchClient(
        "http://user:pass@proxy.example:8080",
        "test-agent",
        3,
        0,
        session=_SearchSession([_Response(407, {})]),  # type: ignore[arg-type]
    )

    with pytest.raises(SearchError, match="HTTP 407"):
        client.search_page("title:test", 10, 0)


def test_details_request_uses_existing_proxy_client_and_required_headers() -> None:
    proxy_url = "http://encoded-user:encoded-password@proxy.example:8080"
    session = _SearchSession([_Response(200, {"key": "/works/OL1W"})])
    client = OpenLibrarySearchClient(
        proxy_url, "configured-agent", 3, 0, session=session  # type: ignore[arg-type]
    )
    headers = {
        "User-Agent": "PostmanRuntime/2.12.2",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    payload = client.get_work_details("OL1W")

    assert payload == {"key": "/works/OL1W"}
    assert session.calls[0]["url"] == "https://openlibrary.org/works/OL1W.json"
    assert session.calls[0]["headers"] == headers
    assert session.calls[0]["proxies"] == {"http": proxy_url, "https": proxy_url}


def test_editions_request_uses_configured_api_and_page_size() -> None:
    session = _SearchSession([_Response(200, {"size": 0, "entries": []})])
    client = OpenLibrarySearchClient(
        "******proxy.example:8080",
        "configured-agent",
        3,
        0,
        session=session,  # type: ignore[arg-type]
        api_base_url="https://api.example/v1/",
        edition_page_size=25,
    )

    client.get_editions_page("OL1W", offset=50)

    assert session.calls[0]["url"] == "https://api.example/v1/works/OL1W/editions.json"
    assert session.calls[0]["params"] == {"limit": 25, "offset": 50}
    assert session.calls[0]["proxies"] == {
        "http": "******proxy.example:8080",
        "https": "******proxy.example:8080",
    }


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (requests.exceptions.ConnectTimeout, "timed out"),
        (requests.exceptions.ConnectionError, "connection"),
    ],
)
def test_search_client_reports_proxy_connection_failures(failure, message: str) -> None:
    class FailingSession:
        headers: dict[str, str] = {}

        def get(self, url: str, **kwargs: Any) -> _Response:
            raise failure()

    client = OpenLibrarySearchClient(
        "http://user:pass@proxy.example:8080",
        "test-agent",
        3,
        0,
        session=FailingSession(),  # type: ignore[arg-type]
    )

    with pytest.raises(SearchError, match=message):
        client.search_page("title:test", 10, 0)


class _OnePageClient:
    edition_page_size = 100

    def __init__(self) -> None:
        self.request_count = 0

    def search_page(self, query: str, limit: int, offset: int) -> SearchPage:
        self.request_count += 1
        assert offset == 0
        return parse_search_page(
            {
                "numFound": 1,
                "numFoundExact": True,
                "start": 0,
                "docs": [
                    {
                        "key": "/works/OL27482W",
                        "title": "The Hobbit",
                        "author_name": ["J. R. R. Tolkien"],
                        "publisher": ["Houghton Mifflin"],
                        "publish_year": [2001],
                        "language": ["eng"],
                        "subject": ["Fantasy"],
                    }
                ],
            }
        )

    def get_json(self, url: str, params=None, headers=None) -> dict[str, Any]:
        self.request_count += 1
        if url.endswith("/works/OL27482W.json"):
            return {
                "key": "/works/OL27482W",
                "title": "The Hobbit",
                "description": {"value": "A fantasy novel."},
                "subjects": ["Fantasy"],
            }
        return {
            "size": 1,
            "entries": [
                {
                    "key": "/books/OL123M",
                    "publishers": ["Houghton Mifflin"],
                    "publish_date": "2001",
                    "languages": [{"key": "/languages/eng"}],
                    "number_of_pages": 300,
                }
            ],
        }

    def get_work_details(self, work_id: str) -> dict[str, Any]:
        return self.get_json(work_details_url(work_id))

    def get_editions_page(self, work_id: str, offset: int) -> dict[str, Any]:
        return self.get_json(
            editions_url(work_id),
            params={"limit": self.edition_page_size, "offset": offset},
        )


def test_full_pipeline_creates_expected_outputs(tmp_path: Path) -> None:
    input_path = tmp_path / "books.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "book_title",
            "author",
            "target_publisher",
            "target_publication_year",
            "target_language_code",
            "subject",
        ]
    )
    sheet.append(
        ["The Hobbit", "J. R. R. Tolkien", "Houghton Mifflin", 2001, "eng", "Fantasy"]
    )
    workbook.save(input_path)

    output_dir = tmp_path / "output"
    settings = Settings(
        project_root=tmp_path,
        proxy_url="http://user:pass@proxy.example:8080",
        request_timeout=1,
        max_retries=0,
        page_size=100,
        max_pages=10,
        resume_enabled=True,
        start_fresh=False,
        log_level="INFO",
        user_agent="test-agent",
        input_file=input_path,
        output_dir=output_dir,
        high_confidence_threshold=0.85,
        possible_match_threshold=0.60,
        ambiguity_delta=0.03,
    )
    checkpoints = CheckpointStore(output_dir)
    checkpoints.initialize(resume=False, fresh=True)
    raw_store = RawResponseStore(output_dir)
    client = _OnePageClient()
    pagination = PaginationService(client, raw_store, checkpoints, page_size=100, max_pages=10)
    metadata_service = OpenLibraryMetadataService(client, raw_store, checkpoints, max_pages=10)
    report = SearchPipeline(
        settings,
        pagination,
        raw_store,
        checkpoints,
        {
            "provider_batches": 0,
            "provider_http_requests": 0,
            "provided": 1,
            "validated": 0,
            "failed": 0,
            "rotations": 0,
        },
        lambda: client.request_count,
        metadata_service,
    ).run()

    assert report["successfully_processed_books"] == 1
    assert report["total_work_candidates"] == 1
    assert (output_dir / "search_results.csv").is_file()
    assert (output_dir / "best_work_matches.csv").is_file()
    assert (output_dir / "execution_report.json").is_file()
    final_path = output_dir / "final_books.json"
    assert final_path.is_file()
    final_books = json.loads(final_path.read_text(encoding="utf-8"))
    assert len(final_books) == 1
    assert set(final_books[0]) == {
        "title",
        "author",
        "publisher",
        "publication_date",
        "language",
        "number_of_pages",
        "subjects",
        "description",
        "edition_url",
        "work_url",
    }
    assert final_books[0]["edition_url"] == "https://openlibrary.org/books/OL123M"
    assert report["metadata_records"] == 1
    best = load_workbook(output_dir / "best_work_matches.xlsx", data_only=True)
    headers = [cell.value for cell in best.active[1]]
    work_id_column = headers.index("selected_work_id") + 1
    assert best.active.cell(row=2, column=work_id_column).value == "OL27482W"
    assert report["output_paths"]["execution_report"].endswith("execution_report.json")
