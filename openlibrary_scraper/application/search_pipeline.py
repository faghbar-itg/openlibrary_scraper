from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable

from openlibrary_scraper.config.settings import Settings
from openlibrary_scraper.domain.models.book import BookRecord
from openlibrary_scraper.domain.models.match_result import MatchResult
from openlibrary_scraper.domain.models.work import Work
from openlibrary_scraper.domain.services.query_builder import build_search_queries
from openlibrary_scraper.domain.services.scoring_service import score_work, select_matches
from openlibrary_scraper.infrastructure.storage.checkpoint_store import CheckpointStore
from openlibrary_scraper.infrastructure.storage.raw_response_store import RawResponseStore
from openlibrary_scraper.application.pagination_service import (
    PaginationService,
    RetrievalResult,
)
from openlibrary_scraper.application.metadata_service import OpenLibraryMetadataService
from openlibrary_scraper.processing.input_reader import read_books
from openlibrary_scraper.reporting.exporter import export_results
from openlibrary_scraper.utils.errors import ScraperError


LOGGER = logging.getLogger(__name__)


def _joined(values: tuple[str, ...] | tuple[int, ...]) -> str:
    return "; ".join(str(value) for value in values)


class SearchPipeline:
    def __init__(
        self,
        settings: Settings,
        pagination: PaginationService,
        raw_store: RawResponseStore,
        checkpoints: CheckpointStore,
        proxy_stats: dict[str, int],
        request_counter: Callable[[], int],
        metadata_service: OpenLibraryMetadataService,
    ) -> None:
        self.settings = settings
        self.pagination = pagination
        self.raw_store = raw_store
        self.checkpoints = checkpoints
        self.proxy_stats = proxy_stats
        self.request_counter = request_counter
        self.metadata_service = metadata_service

    @staticmethod
    def _base_input_row(book: BookRecord) -> dict[str, Any]:
        return {
            "input_book_id": book.book_id,
            "source_row": book.source_row,
            **book.original,
            "input_valid": book.valid,
            "validation_errors": "; ".join(book.validation_errors),
        }

    @staticmethod
    def _candidate_row(
        book: BookRecord, result: MatchResult
    ) -> dict[str, Any]:
        work = result.work
        return {
            **SearchPipeline._base_input_row(book),
            "work_id": work.work_id,
            "work_url": work.work_url,
            "work_title": work.title,
            "author_name": _joined(work.authors),
            "publisher": _joined(work.publishers),
            "publish_year": _joined(work.publication_years),
            "language": _joined(work.languages),
            "subject": _joined(work.subjects),
            "title_score": result.title_score,
            "author_score": result.author_score,
            "publisher_score": result.publisher_score,
            "year_score": result.year_score,
            "language_score": result.language_score,
            "subject_score": result.subject_score,
            "match_score": result.overall_score,
            "match_status": result.status,
            "match_reason": result.reason,
            "raw_work": json.dumps(work.raw, ensure_ascii=False),
        }

    def _best_row(
        self,
        book: BookRecord,
        chosen: MatchResult | None,
        status: str,
        reason: str,
        retrieval: RetrievalResult | None,
        candidate_count: int,
    ) -> dict[str, Any]:
        row = {
            **self._base_input_row(book),
            "selected_work_id": chosen.work.work_id if chosen else None,
            "selected_work_url": chosen.work.work_url if chosen else None,
            "selected_work_title": chosen.work.title if chosen else None,
            "selected_author": _joined(chosen.work.authors) if chosen else None,
            "match_score": chosen.overall_score if chosen else 0.0,
            "match_status": status,
            "match_reason": reason,
            "candidate_count": candidate_count,
            "retrieval_completeness_status": (
                retrieval.pagination_status if retrieval else "NOT_ATTEMPTED"
            ),
            "search_query": retrieval.query if retrieval else None,
            "search_queries": " || ".join(retrieval.queries) if retrieval else None,
            "expected_total": retrieval.expected_total if retrieval else None,
            "retrieved_total": retrieval.retrieved_total if retrieval else 0,
            "retrieved_pages": retrieval.page_count if retrieval else 0,
            "retrieval_errors": "; ".join(retrieval.errors) if retrieval else "",
        }
        return row

    def run(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        books = read_books(self.settings.input_file)
        LOGGER.info("Loaded %s input books.", len(books))
        candidate_rows: list[dict[str, Any]] = []
        best_rows: list[dict[str, Any]] = []
        final_books: list[dict[str, Any]] = []
        metadata_processing: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        warnings: list[str] = []
        processed = 0
        complete_searches = 0
        incomplete_searches = 0
        global_errors: list[str] = []

        for book in books:
            LOGGER.info("Processing %s (%s).", book.book_id, book.title)
            if not book.valid:
                reason = "; ".join(book.validation_errors)
                final_books.append(self.metadata_service.empty_record())
                best_rows.append(
                    self._best_row(book, None, "NO_MATCH", reason, None, 0)
                )
                metadata_processing.append(
                    {"book_id": book.book_id, "status": "INVALID_INPUT", "errors": [reason]}
                )
                errors.append({"book_id": book.book_id, "error": reason})
                state = self.checkpoints.book_state(book.book_id)
                state["status"] = "invalid"
                state["metadata"] = {"status": "INVALID_INPUT", "errors": [reason]}
                self.checkpoints.save()
                continue

            retrieval: RetrievalResult | None = None
            try:
                queries = build_search_queries(book)
                retrieval = self.pagination.retrieve(book.book_id, queries)
                scored = [
                    score_work(book, work, self.settings)
                    for work in retrieval.works
                ]
                for result in scored:
                    candidate_rows.append(self._candidate_row(book, result))
                chosen, status = select_matches(
                    scored, self.settings.ambiguity_delta
                )
                reason = chosen.reason if chosen else "No candidate met the configured match threshold."
                ranked = sorted(
                    (match for match in scored if match.work.work_id),
                    key=lambda match: match.overall_score,
                    reverse=True,
                )
                if (
                    len(ranked) > 1
                    and ranked[0].work.work_id
                    and ranked[1].work.work_id
                    and ranked[0].overall_score - ranked[1].overall_score
                    <= self.settings.ambiguity_delta
                ):
                    reason += (
                        f"; ambiguous top candidates within "
                        f"{self.settings.ambiguity_delta:.3f} score points"
                    )
                    if status == "HIGH_CONFIDENCE":
                        status = "POSSIBLE_MATCH"
                if not retrieval.complete:
                    warnings.append(
                        f"{book.book_id}: search incomplete; selected match may be based on partial results."
                    )
                warnings.extend(
                    f"{book.book_id}: {warning}" for warning in retrieval.uncertainty
                )
                errors.extend(
                    {"book_id": book.book_id, "error": error}
                    for error in retrieval.errors
                )
                final_record, metadata_status, metadata_errors, work_id, edition_id = (
                    self.metadata_service.retrieve(book, chosen.work if chosen else None)
                )
                final_books.append(final_record)
                metadata_processing.append(
                    {
                        "book_id": book.book_id,
                        "status": metadata_status,
                        "work_id": work_id,
                        "edition_id": edition_id,
                        "errors": metadata_errors,
                    }
                )
                errors.extend(
                    {"book_id": book.book_id, "error": f"Metadata: {error}"}
                    for error in metadata_errors
                )
                complete_searches += int(retrieval.complete)
                incomplete_searches += int(not retrieval.complete)
                best_rows.append(
                    self._best_row(
                        book, chosen, status, reason, retrieval, len(scored)
                    )
                )
                processed += int(retrieval.complete)
                state = self.checkpoints.book_state(book.book_id)
                state["status"] = "complete" if retrieval.complete else "incomplete"
                state["selected_work_id"] = (
                    chosen.work.work_id if chosen else None
                )
                self.checkpoints.save()
                LOGGER.info(
                    "%s retrieved %s candidates across %s pages "
                    "(expected %s, %s); match=%s score=%.3f.",
                    book.book_id,
                    len(scored),
                    retrieval.page_count,
                    retrieval.expected_total if retrieval.expected_total is not None else "unknown",
                    retrieval.pagination_status,
                    status,
                    chosen.overall_score if chosen else 0.0,
                )
            except ScraperError as exc:
                message = f"{type(exc).__name__}: {exc}"
                LOGGER.error("%s failed: %s", book.book_id, message)
                errors.append({"book_id": book.book_id, "error": message})
                final_books.append(self.metadata_service.empty_record())
                metadata_processing.append(
                    {"book_id": book.book_id, "status": "FAILED_SEARCH", "errors": [message]}
                )
                best_rows.append(
                    self._best_row(book, None, "NO_MATCH", message, retrieval, 0)
                )
                state = self.checkpoints.book_state(book.book_id)
                state["status"] = "failed"
                state["metadata"] = {"status": "FAILED_SEARCH", "errors": [message]}
                self.checkpoints.save()

        represented_books = {
            row["input_book_id"] for row in best_rows
        }
        global_status = "NOT_ATTEMPTED"
        unattempted_reason = (
            "Search not attempted because proxy infrastructure was unavailable."
            if global_errors
            else "Search was not attempted."
        )
        for book in books:
            if book.book_id not in represented_books:
                best_rows.append(
                    self._best_row(
                        book,
                        None,
                        global_status,
                        unattempted_reason,
                        None,
                        0,
                    )
                )

        match_counts = Counter(row["match_status"] for row in best_rows)
        ended = datetime.now(timezone.utc)
        report: dict[str, Any] = {
            "run_id": self.checkpoints.run_id,
            "execution_status": (
                "GLOBAL_INFRASTRUCTURE_FAILURE"
                if global_errors
                else ("COMPLETED_WITH_ERRORS" if errors else "COMPLETED")
            ),
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "total_input_books": len(books),
            "successfully_processed_books": processed,
            "failed_books": len({error["book_id"] for error in errors}),
            "total_openlibrary_requests": self.request_counter(),
            "total_pages_retrieved": sum(
                int(row.get("retrieved_pages") or 0) for row in best_rows
            ),
            "total_work_candidates": len(candidate_rows),
            "metadata_records": len(final_books),
            "metadata_status_counts": dict(
                Counter(item["status"] for item in metadata_processing)
            ),
            "complete_searches": complete_searches,
            "incomplete_searches": incomplete_searches,
            "match_status_counts": dict(match_counts),
            "proxy_statistics": dict(self.proxy_stats),
            "errors": errors,
            "global_errors": global_errors,
            "warnings": list(dict.fromkeys(warnings)),
            "metadata_processing": metadata_processing,
        }
        report["output_paths"] = {
            "search_results_excel": str(self.settings.output_dir / "search_results.xlsx"),
            "search_results_csv": str(self.settings.output_dir / "search_results.csv"),
            "best_matches_excel": str(self.settings.output_dir / "best_work_matches.xlsx"),
            "best_matches_csv": str(self.settings.output_dir / "best_work_matches.csv"),
            "execution_report": str(self.settings.output_dir / "execution_report.json"),
            "final_books": str(self.settings.output_dir / "final_books.json"),
        }
        self.checkpoints.state["last_execution"] = {
            "status": report["execution_status"],
            "global_errors": global_errors,
        }
        self.checkpoints.save()
        export_results(
            self.settings.output_dir,
            candidate_rows,
            best_rows,
            report,
            final_books,
        )
        LOGGER.info("Outputs written under %s.", self.settings.output_dir)
        return report
