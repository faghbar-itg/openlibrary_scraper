from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openlibrary_scraper.requests.response_parser import (
    SearchPage,
    Work,
    parse_search_page,
)
from openlibrary_scraper.requests.client import OpenLibrarySearchClient
from openlibrary_scraper.processing.storage import CheckpointStore, RawResponseStore
from openlibrary_scraper.errors import SearchError


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    queries: tuple[str, ...]
    works: tuple[Work, ...]
    expected_total: int | None
    retrieved_total: int
    page_count: int
    pagination_status: str
    complete: bool
    uncertainty: tuple[str, ...]
    errors: tuple[str, ...]


class PaginationService:
    def __init__(
        self,
        client: OpenLibrarySearchClient,
        raw_store: RawResponseStore,
        checkpoints: CheckpointStore,
        page_size: int,
        max_pages: int,
    ) -> None:
        self.client = client
        self.raw_store = raw_store
        self.checkpoints = checkpoints
        self.page_size = page_size
        self.max_pages = max_pages

    def _get_page(
        self,
        book_id: str,
        query: str,
        page_index: int,
        offset: int,
        query_state: dict[str, Any],
    ) -> SearchPage:
        saved_pages = query_state.setdefault("pages", [])
        if page_index < len(saved_pages):
            payload = self.raw_store.load_page(
                self.checkpoints.run_id, book_id, page_index, query
            )
            return parse_search_page(payload)

        page = self.client.search_page(query, self.page_size, offset)
        self.raw_store.save_page(
            self.checkpoints.run_id, book_id, page_index, page.raw, query
        )
        saved_pages.append({"offset": offset, "start": page.start})
        self.checkpoints.save()
        return page

    def _retrieve_query(
        self, book_id: str, query: str
    ) -> tuple[list[Work], int | None, int, bool, list[str], list[str]]:
        book_state = self.checkpoints.book_state(book_id)
        query_state = book_state.setdefault("queries", {}).setdefault(
            query, {"pages": [], "expected_total": None, "complete": False}
        )
        expected = query_state.get("expected_total")
        works_by_id: dict[str, Work] = {}
        anonymous_works: list[Work] = []
        uncertainty: list[str] = []
        errors: list[str] = []
        retrieved = 0
        offset = 0

        for page_index in range(self.max_pages):
            try:
                page = self._get_page(
                    book_id, query, page_index, offset, query_state
                )
            except SearchError as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                break

            LOGGER.info(
                "%s page %s: requested offset %s, received %s works, expected total %s.",
                book_id,
                page_index + 1,
                offset,
                len(page.docs),
                page.num_found if page.num_found is not None else "unknown",
            )
            if expected is not None and page.num_found is not None and expected != page.num_found:
                uncertainty.append(
                    f"numFound changed from {expected} to {page.num_found}"
                )
            if page.num_found is not None:
                expected = page.num_found
                query_state["expected_total"] = expected
            else:
                uncertainty.append("Open Library response omitted numFound")
            if page.num_found_exact is False:
                uncertainty.append("Open Library marked numFound as inexact")
            start_mismatch = page.start is not None and page.start != offset
            if start_mismatch:
                uncertainty.append(
                    f"response start {page.start} differed from requested offset {offset}"
                )

            actual_start = page.start if page.start is not None else offset
            retrieved += len(page.docs)
            for work in page.docs:
                self._add_work(work, works_by_id, anonymous_works)

            if start_mismatch:
                errors.append("Inconsistent response start prevents completeness validation")
                query_state["complete"] = False
                break

            next_offset = actual_start + len(page.docs)
            if not page.docs:
                if expected is not None and next_offset < expected:
                    uncertainty.append("empty page before expected result total")
                    errors.append("Search ended before all expected results were retrieved")
                query_state["complete"] = not errors
                break
            if page.num_found_exact is True and expected is not None and next_offset >= expected:
                query_state["complete"] = True
                break
            if page.num_found_exact is None and len(page.docs) < self.page_size:
                if expected is not None and next_offset < expected:
                    uncertainty.append("short page before expected result total")
                    errors.append("Search ended before all expected results were retrieved")
                query_state["complete"] = not errors
                break

            offset = next_offset
        else:
            uncertainty.append(f"maximum page count ({self.max_pages}) reached")
            errors.append("Maximum page count reached before completion")

        query_state["expected_total"] = expected
        book_state["status"] = "searching"
        self.checkpoints.save()
        return (
            list(works_by_id.values()) + anonymous_works,
            expected,
            retrieved,
            bool(query_state.get("complete")),
            uncertainty,
            errors,
        )

    @staticmethod
    def _add_work(
        work: Work, works_by_id: dict[str, Work], anonymous_works: list[Work]
    ) -> None:
        if work.work_id:
            works_by_id.setdefault(work.work_id, work)
        else:
            anonymous_works.append(work)

    def retrieve(self, book_id: str, queries: tuple[str, ...]) -> RetrievalResult:
        all_works: list[Work] = []
        uncertainty: list[str] = []
        errors: list[str] = []
        expected: int | None = None
        retrieved = 0
        page_count = 0
        complete = False
        successful_query = queries[0]

        for query in queries:
            successful_query = query
            works, expected, retrieved, complete, notes, failures = self._retrieve_query(
                book_id, query
            )
            all_works = works
            uncertainty.extend(notes)
            errors.extend(failures)
            page_count += len(
                self.checkpoints.book_state(book_id)["queries"][query]["pages"]
            )
            if works or query == queries[-1]:
                break

        status = "COMPLETE" if complete else ("INCOMPLETE" if errors or uncertainty else "UNKNOWN")
        query_results = {}
        saved_queries = self.checkpoints.book_state(book_id)["queries"]
        for query in queries:
            query_state = saved_queries.get(query, {})
            query_results[query] = {
                "expected_total": query_state.get("expected_total"),
                "pages": len(query_state.get("pages", [])),
                "complete": bool(query_state.get("complete")),
            }
        self.raw_store.save_metadata(
            self.checkpoints.run_id,
            book_id,
            {
                "query": successful_query,
                "queries": list(queries),
                "query_results": query_results,
                "expected_total": expected,
                "retrieved_total": retrieved,
                "unique_work_candidates": len(all_works),
                "pages": page_count,
                "pagination_status": status,
                "complete": complete,
                "uncertainty": list(dict.fromkeys(uncertainty)),
                "errors": errors,
            },
        )
        return RetrievalResult(
            successful_query,
            queries,
            tuple(all_works),
            expected,
            retrieved,
            page_count,
            status,
            complete,
            tuple(dict.fromkeys(uncertainty)),
            tuple(errors),
        )
