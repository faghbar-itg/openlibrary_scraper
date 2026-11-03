from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
import pytest

from openlibrary_scraper.reporting.exporter import export_results
from openlibrary_scraper.utils.errors import OutputError


def test_export_results_removes_excel_illegal_characters_only_from_workbooks(
    tmp_path,
) -> None:
    illegal_text = "Before\x01 Arabic العربية\tline one\nline two\x0b!"
    candidate_rows = [{"work_title": illegal_text, "work_id": "OL1W"}]
    best_rows = [{"selected_work_title": illegal_text, "status": "HIGH_CONFIDENCE"}]
    report = {"message": illegal_text}
    final_books = [
        {
            "title": "The Hobbit",
            "author": "J. R. R. Tolkien",
            "publisher": "HarperCollins",
            "publication_date": "1937",
            "language": "English",
            "number_of_pages": 310,
            "subjects": ["Fantasy"],
            "description": None,
            "edition_url": "https://openlibrary.org/books/OL1M",
            "work_url": "https://openlibrary.org/works/OL1W",
        }
    ]

    paths = export_results(tmp_path, candidate_rows, best_rows, report, final_books)

    expected_excel_text = "Before Arabic العربية\tline one\nline two!"
    for key, column in (
        ("search_results_excel", "work_title"),
        ("best_matches_excel", "selected_work_title"),
    ):
        workbook = load_workbook(paths[key], read_only=True, data_only=True)
        worksheet = workbook.active
        headers = [cell.value for cell in worksheet[1]]
        value = worksheet.cell(row=2, column=headers.index(column) + 1).value
        workbook.close()
        assert value == expected_excel_text

    for key in (
        "search_results_excel",
        "search_results_csv",
        "best_matches_excel",
        "best_matches_csv",
        "execution_report",
        "final_books",
    ):
        assert Path(paths[key]).is_file()

    assert illegal_text.encode("utf-8") in (tmp_path / "search_results.csv").read_bytes()
    assert json.loads((tmp_path / "execution_report.json").read_text(encoding="utf-8")) == report
    final_json = json.loads((tmp_path / "final_books.json").read_text(encoding="utf-8"))
    assert final_json == final_books
    assert len(final_json) == 1
    assert set(final_json[0]) == set(final_books[0])


def test_locked_excel_file_does_not_prevent_json_and_csv_outputs(
    tmp_path, monkeypatch
) -> None:
    original_to_excel = pd.DataFrame.to_excel

    def fail_best_matches(self, excel_writer, *args, **kwargs):
        if Path(excel_writer).name == "best_work_matches.xlsx":
            raise PermissionError("file is locked")
        return original_to_excel(self, excel_writer, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_excel", fail_best_matches)

    with pytest.raises(OutputError, match="best_work_matches.xlsx"):
        export_results(
            tmp_path,
            [{"work_title": "A book"}],
            [{"selected_work_title": "A book"}],
            {"execution_status": "COMPLETED"},
            [{"title": "A book"}],
        )

    assert (tmp_path / "final_books.json").is_file()
    assert (tmp_path / "execution_report.json").is_file()
    assert (tmp_path / "search_results.csv").is_file()
    assert (tmp_path / "best_work_matches.csv").is_file()
    report = json.loads((tmp_path / "execution_report.json").read_text(encoding="utf-8"))
    assert report["output_errors"][0].startswith("best_work_matches.xlsx:")
