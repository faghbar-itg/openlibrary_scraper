from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


def save_final_books(output_dir: Path, final_books: list[dict[str, Any]]) -> Path:
    """Atomically save the only persistent scraper result."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "final_books.json"
    temporary = output_dir / ".final_books.json.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(final_books, file, ensure_ascii=False, indent=2)
            file.flush()
        temporary.replace(path)
    except Exception as exc:
        traceback.print_exc()
        save_error = exc
        try:
            temporary.unlink(missing_ok=True)
        except Exception as exc:
            traceback.print_exc()
            raise Exception(
                f"Could not save final book metadata to {path}: {save_error}; "
                f"could not remove temporary file {temporary}: {exc}"
            ) from save_error
        raise Exception(f"Could not save final book metadata to {path}: {save_error}") from save_error
    LOGGER.info("Saved %s final book records to %s.", len(final_books), path)
    return path
