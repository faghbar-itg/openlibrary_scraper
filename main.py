from __future__ import annotations

import argparse
import logging
import sys

from openlibrary_scraper.config.settings import load_settings
from openlibrary_scraper.errors import ScraperError
from openlibrary_scraper.processing.pipeline import run_pipeline


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
        logging.basicConfig(
            level=getattr(logging, settings.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        report = run_pipeline(settings, start_fresh=arguments.fresh)

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
