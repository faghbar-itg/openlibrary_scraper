from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from openlibrary_scraper.utils.errors import OutputError


def _excel_safe_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    dataframe = pd.DataFrame(rows)
    return dataframe.map(
        lambda value: ILLEGAL_CHARACTERS_RE.sub("", value)
        if isinstance(value, str)
        else value
    )


def export_results(
    output_dir: Path,
    candidate_rows: list[dict[str, Any]],
    best_rows: list[dict[str, Any]],
    report: dict[str, Any],
    final_books: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "search_results_excel": output_dir / "search_results.xlsx",
        "search_results_csv": output_dir / "search_results.csv",
        "best_matches_excel": output_dir / "best_work_matches.xlsx",
        "best_matches_csv": output_dir / "best_work_matches.csv",
        "execution_report": output_dir / "execution_report.json",
        "final_books": output_dir / "final_books.json",
    }
    try:
        final_temp = paths["final_books"].with_suffix(".json.tmp")
        with final_temp.open("w", encoding="utf-8") as file:
            json.dump(final_books or [], file, ensure_ascii=False, indent=2)
            file.flush()
        final_temp.replace(paths["final_books"])
    except (OSError, ValueError, ImportError) as exc:
        raise OutputError(f"Could not write final book metadata under {output_dir}.") from exc

    exports: tuple[tuple[str, Callable[[], Any]], ...] = (
        (
            "search_results.xlsx",
            lambda: _excel_safe_dataframe(candidate_rows).to_excel(
                paths["search_results_excel"], index=False
            ),
        ),
        (
            "search_results.csv",
            lambda: pd.DataFrame(candidate_rows).to_csv(
                paths["search_results_csv"], index=False, encoding="utf-8-sig"
            ),
        ),
        (
            "best_work_matches.xlsx",
            lambda: _excel_safe_dataframe(best_rows).to_excel(
                paths["best_matches_excel"], index=False
            ),
        ),
        (
            "best_work_matches.csv",
            lambda: pd.DataFrame(best_rows).to_csv(
                paths["best_matches_csv"], index=False, encoding="utf-8-sig"
            ),
        ),
    )
    export_errors: list[str] = []
    for name, write_export in exports:
        try:
            write_export()
        except (OSError, ValueError, ImportError) as exc:
            export_errors.append(f"{name}: {exc}")

    report["output_errors"] = export_errors
    try:
        temp = paths["execution_report"].with_suffix(".json.tmp")
        with temp.open("w", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
            file.flush()
        temp.replace(paths["execution_report"])
    except (OSError, ValueError, ImportError) as exc:
        raise OutputError(f"Could not write the execution report under {output_dir}.") from exc
    if export_errors:
        raise OutputError(
            f"Some output files could not be written under {output_dir}: "
            + "; ".join(export_errors)
        )
    return {key: str(path) for key, path in paths.items()}
