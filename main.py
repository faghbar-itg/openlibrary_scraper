from __future__ import annotations

import argparse
import logging
import sys

import requests

from openlibrary_scraper.application.pagination_service import PaginationService
from openlibrary_scraper.application.metadata_service import OpenLibraryMetadataService
from openlibrary_scraper.application.search_pipeline import SearchPipeline
from openlibrary_scraper.config.settings import load_settings
from openlibrary_scraper.infrastructure.openlibrary.search_client import OpenLibrarySearchClient
from openlibrary_scraper.infrastructure.storage.checkpoint_store import CheckpointStore
from openlibrary_scraper.infrastructure.storage.raw_response_store import RawResponseStore
from openlibrary_scraper.utils.errors import ScraperError
from openlibrary_scraper.utils.logging_config import configure_logging


LOGGER = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search Open Library for input books.")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Start a new run instead of resuming the saved checkpoint.",
    )
    arguments = parser.parse_args()

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        checkpoints = CheckpointStore(settings.output_dir)
        checkpoints.initialize(settings.resume_enabled, settings.start_fresh or arguments.fresh)
        raw_store = RawResponseStore(settings.output_dir)

        with requests.Session() as search_session:
            search_client = OpenLibrarySearchClient(
                settings.proxy_url,
                settings.user_agent,
                settings.request_timeout,
                settings.max_retries,
                session=search_session,
            )
            pagination = PaginationService(
                search_client,
                raw_store,
                checkpoints,
                settings.page_size,
                settings.max_pages,
            )
            metadata_service = OpenLibraryMetadataService(
                search_client,
                raw_store,
                checkpoints,
                settings.max_pages,
            )
            report = SearchPipeline(
                settings,
                pagination,
                raw_store,
                checkpoints,
                search_client.proxy_stats,
                lambda: search_client.request_count,
                metadata_service,
            ).run()

        print(
            f"Run {report['run_id']} finished: "
            f"{report['successfully_processed_books']}/{report['total_input_books']} "
            f"complete searches, {report['total_work_candidates']} candidates."
        )
        print(f"Outputs: {settings.output_dir}")
        return 0
    except ScraperError as exc:
        LOGGER.error("%s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
