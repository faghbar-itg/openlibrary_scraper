from __future__ import annotations

from typing import Any

import pytest

from openlibrary_scraper.processing.metadata import (
    OpenLibraryMetadataService,
    extract_work_id,
    select_edition,
)
from openlibrary_scraper.processing.books import BookRecord
from openlibrary_scraper.requests.response_parser import Work
from openlibrary_scraper.requests.client import (
    DETAIL_HEADERS,
    editions_url,
    work_details_url,
)
from openlibrary_scraper.processing.storage import CheckpointStore, RawResponseStore
from openlibrary_scraper.errors import SearchError


class _Client:
    edition_page_size = 100

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_json(self, url: str, params=None, headers=None) -> dict[str, Any]:
        self.calls.append((url, {"params": params, "headers": headers}))
        return self.responses.pop(0)

    def get_work_details(self, work_id: str) -> dict[str, Any]:
        return self.get_json(work_details_url(work_id), headers=DETAIL_HEADERS)

    def get_editions_page(self, work_id: str, offset: int) -> dict[str, Any]:
        return self.get_json(
            editions_url(work_id),
            params={"limit": self.edition_page_size, "offset": offset},
            headers=DETAIL_HEADERS,
        )


def _book(
    *,
    publisher: str = "HarperCollins",
    year: int | None = 1997,
    language: str = "eng",
    pages: int | None = 300,
) -> BookRecord:
    original: dict[str, Any] = {
        "target_number_of_pages": pages,
    }
    return BookRecord(
        book_id="book_001",
        source_row=2,
        original=original,
        normalized={
            "book_title": "The Example",
            "author": "A. Writer",
            "target_publisher": publisher,
            "target_publication_year": str(year) if year else "",
            "target_language_code": language,
            "subject": "Fantasy",
        },
        publication_year=year,
        valid=True,
    )


def _work(key: str = "/works/OL123W") -> Work:
    return Work(
        work_id=key.rsplit("/", 1)[-1],
        work_url="https://openlibrary.org" + key,
        title="The Example",
        authors=("A. Writer",),
        publishers=("Search Publisher",),
        publication_years=(1997,),
        languages=("eng",),
        subjects=("Search subject",),
        raw={"key": key},
    )


def _service(tmp_path, client: _Client, *, max_pages: int = 5):
    checkpoints = CheckpointStore(tmp_path)
    checkpoints.initialize(resume=False, fresh=True)
    raw_store = RawResponseStore(tmp_path)
    return (
        OpenLibraryMetadataService(client, raw_store, checkpoints, max_pages),
        checkpoints,
        raw_store,
    )


def test_work_id_extraction_and_dynamic_urls() -> None:
    work = _work("https://openlibrary.org/works/OL98765W")

    assert extract_work_id(work) == "OL98765W"
    assert work_details_url("OL98765W") == (
        "https://openlibrary.org/works/OL98765W.json"
    )
    assert editions_url("OL98765W") == (
        "https://openlibrary.org/works/OL98765W/editions.json"
    )


def test_edition_selection_uses_publisher_year_language_and_pages() -> None:
    book = _book()
    editions = [
        {
            "key": "/books/OL1M",
            "publishers": ["Different Press"],
            "publish_date": "1980",
            "languages": [{"key": "/languages/fre"}],
            "number_of_pages": 120,
        },
        {
            "key": "/books/OL2M",
            "publishers": ["HarperCollins Publishers"],
            "publish_date": "1997",
            "languages": [{"key": "/languages/eng"}],
            "number_of_pages": 300,
        },
    ]

    assert select_edition(editions, book) == editions[1]


def test_retrieves_work_and_editions_and_maps_final_metadata(tmp_path) -> None:
    client = _Client(
        [
            {
                "key": "/works/OL123W",
                "title": "The Example",
                "description": {"value": "A detailed description."},
                "subjects": ["Fantasy", "Adventure"],
                "authors": [{"author": {"key": "/authors/OL1A"}}],
                "first_publish_date": "1997",
            },
            {
                "size": 2,
                "entries": [
                    {
                        "key": "/books/OL1M",
                        "publishers": ["Different Press"],
                        "publish_date": "1980",
                        "languages": [{"key": "/languages/fre"}],
                        "number_of_pages": 120,
                    },
                    {
                        "key": "/books/OL2M",
                        "publishers": ["HarperCollins"],
                        "publish_date": "1997",
                        "languages": [{"key": "/languages/eng"}],
                        "number_of_pages": 300,
                    },
                ],
                "links": {},
            },
        ]
    )
    service, checkpoints, raw_store = _service(tmp_path, client)

    record, status, errors, work_id, edition_id = service.retrieve(_book(), _work())

    assert status == "COMPLETED"
    assert errors == []
    assert work_id == "OL123W"
    assert edition_id == "OL2M"
    assert record == {
        "title": "The Example",
        "author": "A. Writer",
        "publisher": "HarperCollins",
        "publication_date": "1997",
        "language": "English",
        "number_of_pages": 300,
        "subjects": ["Fantasy", "Adventure"],
        "description": "A detailed description.",
        "edition_url": "https://openlibrary.org/books/OL2M",
        "work_url": "https://openlibrary.org/works/OL123W",
    }
    assert client.calls[0][0] == work_details_url("OL123W")
    assert client.calls[0][1]["headers"] == DETAIL_HEADERS
    assert client.calls[1][0] == editions_url("OL123W")
    assert client.calls[1][1]["params"] == {"limit": 100, "offset": 0}
    assert raw_store.load_work_details(checkpoints.run_id, "book_001", "OL123W")
    assert raw_store.load_editions_page(checkpoints.run_id, "book_001", "OL123W", 0)
    assert checkpoints.book_state("book_001")["metadata"]["edition_id"] == "OL2M"


def test_retrieves_all_edition_pages_using_offsets(tmp_path) -> None:
    client = _Client(
        [
            {"key": "/works/OL123W", "title": "The Example"},
            {
                "size": 2,
                "entries": [
                    {
                        "key": "/books/OL1M",
                        "publishers": ["Other Press"],
                        "publish_date": "1980",
                        "languages": [{"key": "/languages/fre"}],
                    }
                ],
                "links": {"next": "?offset=1"},
            },
            {
                "size": 2,
                "entries": [
                    {
                        "key": "/books/OL2M",
                        "publishers": ["HarperCollins"],
                        "publish_date": "1997",
                        "languages": [{"key": "/languages/eng"}],
                    }
                ],
                "links": {},
            },
        ]
    )
    service, _, _ = _service(tmp_path, client)

    record, status, _, _, edition_id = service.retrieve(
        _book(pages=None), _work()
    )

    assert status == "COMPLETED"
    assert edition_id == "OL2M"
    assert record["publisher"] == "HarperCollins"
    assert [call[1]["params"]["offset"] for call in client.calls[1:]] == [0, 1]


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Plain description", "Plain description"),
        ({"value": "Object description"}, "Object description"),
    ],
)
def test_description_accepts_string_and_value_object(
    tmp_path, description: object, expected: str
) -> None:
    client = _Client([])
    service, _, _ = _service(tmp_path, client)
    record = service._build_record(
        _book(),
        _work(),
        {"description": description},
        None,
        None,
    )

    assert record["description"] == expected


def test_missing_work_metadata_has_nulls_empty_subjects_and_no_edition(tmp_path) -> None:
    client = _Client([{"key": "/works/OL123W"}, {"size": 0, "entries": []}])
    service, _, _ = _service(tmp_path, client)
    work = Work(
        work_id="OL123W",
        work_url="https://openlibrary.org/works/OL123W",
        title="",
        authors=(),
        publishers=(),
        publication_years=(),
        languages=(),
        subjects=(),
        raw={"key": "/works/OL123W"},
    )

    record, status, errors, _, edition_id = service.retrieve(_book(), work)

    assert status == "NO_EDITION"
    assert errors == []
    assert edition_id is None
    assert record == {
        "title": None,
        "author": None,
        "publisher": None,
        "publication_date": None,
        "language": None,
        "number_of_pages": None,
        "subjects": [],
        "description": None,
        "edition_url": None,
        "work_url": "https://openlibrary.org/works/OL123W",
    }


def test_no_matching_edition_keeps_work_metadata_and_null_edition_url(tmp_path) -> None:
    client = _Client(
        [
            {
                "key": "/works/OL123W",
                "title": "The Example",
                "first_publish_date": "1890",
                "subjects": ["Classic"],
                "description": "Work description",
            },
            {
                "size": 1,
                "entries": [
                    {
                        "key": "/books/OL9M",
                        "publishers": ["Unrelated Press"],
                        "publish_date": "1980",
                        "languages": [{"key": "/languages/fre"}],
                    }
                ],
            },
        ]
    )
    service, _, _ = _service(tmp_path, client)

    record, status, errors, _, edition_id = service.retrieve(_book(), _work())

    assert status == "NO_SUITABLE_EDITION"
    assert errors == []
    assert edition_id is None
    assert record["publisher"] == "Search Publisher"
    assert record["publication_date"] == "1890"
    assert record["language"] == "English"
    assert record["edition_url"] is None
    assert record["work_url"] == "https://openlibrary.org/works/OL123W"


def test_missing_work_details_is_reported_without_losing_search_metadata(tmp_path) -> None:
    class MissingWorkClient:
        edition_page_size = 100

        def __init__(self) -> None:
            self.edition_calls = 0

        def get_json(self, url: str, params=None, headers=None) -> dict[str, Any]:
            if url.endswith(".json") and not url.endswith("/editions.json"):
                raise SearchError("Open Library record was not found.")
            self.edition_calls += 1
            return {"size": 0, "entries": []}

        def get_work_details(self, work_id: str) -> dict[str, Any]:
            return self.get_json(work_details_url(work_id), headers=DETAIL_HEADERS)

        def get_editions_page(self, work_id: str, offset: int) -> dict[str, Any]:
            return self.get_json(
                editions_url(work_id),
                params={"limit": self.edition_page_size, "offset": offset},
                headers=DETAIL_HEADERS,
            )

    client = MissingWorkClient()
    service, _, _ = _service(tmp_path, client)  # type: ignore[arg-type]

    record, status, errors, work_id, edition_id = service.retrieve(_book(), _work())

    assert status == "PARTIAL"
    assert work_id == "OL123W"
    assert edition_id is None
    assert errors == ["Work details: Open Library record was not found."]
    assert record["title"] == "The Example"
    assert record["author"] == "A. Writer"
    assert record["publisher"] == "Search Publisher"
    assert record["edition_url"] is None


def test_cached_work_and_edition_responses_are_reused_on_restart(tmp_path) -> None:
    first_client = _Client(
        [
            {"key": "/works/OL123W", "title": "The Example"},
            {
                "size": 1,
                "entries": [
                    {
                        "key": "/books/OL2M",
                        "publishers": ["HarperCollins"],
                        "publish_date": "1997",
                        "languages": [{"key": "/languages/eng"}],
                    }
                ],
            },
        ]
    )
    service, checkpoints, raw_store = _service(tmp_path, first_client)
    service.retrieve(_book(pages=None), _work())

    restart_client = _Client([])
    restarted_service = OpenLibraryMetadataService(
        restart_client, raw_store, checkpoints, max_pages=5
    )
    record, status, errors, _, edition_id = restarted_service.retrieve(
        _book(pages=None), _work()
    )

    assert restart_client.calls == []
    assert status == "COMPLETED"
    assert errors == []
    assert edition_id == "OL2M"
    assert record["title"] == "The Example"
