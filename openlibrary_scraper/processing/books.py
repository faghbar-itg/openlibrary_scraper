from __future__ import annotations

import logging
import math
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from openlibrary_scraper.errors import InputValidationError


_NON_ALPHANUMERIC = re.compile(r"[^\w]+", flags=re.UNICODE)
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class BookRecord:
    book_id: str
    source_row: int
    original: dict[str, Any]
    normalized: dict[str, str]
    publication_year: int | None
    valid: bool
    validation_errors: tuple[str, ...] = ()
    duplicate_of: str | None = None

    @property
    def title(self) -> str:
        return self.normalized["book_title"]

    @property
    def author(self) -> str:
        return self.normalized["author"]


def normalize_source_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = unicodedata.normalize("NFKC", str(value))
    return _SPACE.sub(" ", text).strip()


def normalize_match_text(value: object) -> str:
    text = normalize_source_text(value).casefold()
    text = _NON_ALPHANUMERIC.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


LOGGER = logging.getLogger(__name__)
REQUIRED_COLUMNS = (
    "book_title",
    "author",
    "target_publisher",
    "target_publication_year",
    "target_language_code",
    "subject",
)
OPTIONAL_COLUMNS = ("number_of_pages", "target_number_of_pages")


def _cell_value(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _value_at(row: tuple[Any, ...], position: int) -> Any:
    return _cell_value(row[position] if position < len(row) else None)


def _year(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        numeric = float(value)
        if not numeric.is_integer():
            return None
        year = int(numeric)
    except (TypeError, ValueError):
        return None
    return year if 0 < year <= 9999 else None


def read_books(path: Path) -> list[BookRecord]:
    if not path.is_file():
        raise InputValidationError(f"Input workbook does not exist: {path}")
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except (OSError, ValueError, InvalidFileException, zipfile.BadZipFile) as exc:
        raise InputValidationError(f"Could not open input workbook: {path}") from exc

    try:
        return _read_workbook_records(workbook)
    finally:
        workbook.close()


def _read_workbook_records(workbook: Any) -> list[BookRecord]:
    worksheet = workbook.active
    rows = worksheet.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration as exc:
        raise InputValidationError("Input workbook is empty.") from exc

    headers = [normalize_source_text(value).lower() for value in header_row]
    positions = {header: index for index, header in enumerate(headers) if header}
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in positions]
    if missing_columns:
        raise InputValidationError(
            "Missing required columns: " + ", ".join(missing_columns)
        )

    books: list[BookRecord] = []
    seen: dict[tuple[str, ...], str] = {}
    for row_number, values in enumerate(rows, start=2):
        record = {
            column: _value_at(values, positions[column])
            for column in REQUIRED_COLUMNS
        }
        record.update(
            {
                column: _value_at(values, positions[column])
                for column in OPTIONAL_COLUMNS
                if column in positions
            }
        )
        if all(value is None or not normalize_source_text(value) for value in record.values()):
            LOGGER.warning("Skipping completely empty input row %s.", row_number)
            continue

        normalized = {key: normalize_source_text(value) for key, value in record.items()}
        normalized["book_title"] = normalize_source_text(record["book_title"])
        errors: list[str] = []
        for required in ("book_title", "author"):
            if not normalized[required]:
                errors.append(f"Missing required value: {required}")
        year = _year(record["target_publication_year"])
        if normalized["target_publication_year"] and year is None:
            errors.append("Invalid target_publication_year")
        key = tuple(normalized[column].casefold() for column in REQUIRED_COLUMNS)
        book_id = f"book_{len(books) + 1:03d}"
        duplicate_of = seen.get(key)
        if duplicate_of:
            errors.append(f"Duplicate input record of {duplicate_of}")
        else:
            seen[key] = book_id
        if "\ufffd" in " ".join(normalized.values()):
            errors.append("Input contains Unicode replacement character U+FFFD")
            LOGGER.warning(
                "Input row %s contains U+FFFD; original value will be preserved.", row_number
            )
        books.append(
            BookRecord(
                book_id=book_id,
                source_row=row_number,
                original=record,
                normalized=normalized,
                publication_year=year,
                valid=not errors,
                validation_errors=tuple(errors),
                duplicate_of=duplicate_of,
            )
        )
    return books
