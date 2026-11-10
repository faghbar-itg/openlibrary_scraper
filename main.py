from __future__ import annotations

import logging
import sys
import traceback

from config.settings import load_settings
from processing.data_processor import run_pipeline


LOGGER = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        LOGGER.info("Starting Open Library Scraper.")
        LOGGER.info("Loading configuration.")
        settings = load_settings()
        logging.getLogger().setLevel(getattr(logging, settings.log_level, logging.INFO))
        LOGGER.info("Node4 proxy configuration loaded.")
        LOGGER.info("Input file: %s", settings.input_file)
        LOGGER.info("Page size: %s", settings.page_size)
        LOGGER.info("Maximum pages: %s", settings.max_pages)
        report = run_pipeline(settings)

        LOGGER.info(
            "Execution finished with status %s; %s/%s complete searches, %s candidates.",
            report["execution_status"],
            report["successfully_processed_books"],
            report["total_input_books"],
            report["total_work_candidates"],
        )
        print(
            f"Finished: {report['successfully_processed_books']}/"
            f"{report['total_input_books']} complete searches, "
            f"{report['total_work_candidates']} candidates."
        )
        print(f"Final results: {report['output_file']}")
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
