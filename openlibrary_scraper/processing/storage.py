from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from openlibrary_scraper.errors import OutputError


_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


class CheckpointStore:
    def __init__(self, output_dir: Path) -> None:
        self.path = output_dir / "checkpoints" / "current.json"
        self.state: dict[str, Any] = {}

    def initialize(self, resume: bool, fresh: bool) -> dict[str, Any]:
        if resume and not fresh and self.path.is_file():
            try:
                with self.path.open("r", encoding="utf-8") as file:
                    state = json.load(file)
                if isinstance(state, dict) and isinstance(state.get("books"), dict):
                    self.state = state
                    return self.state
            except (OSError, json.JSONDecodeError) as exc:
                raise OutputError(f"Could not read checkpoint file {self.path}.") from exc
        self.state = {"run_id": uuid.uuid4().hex, "books": {}}
        self.save()
        return self.state

    @property
    def run_id(self) -> str:
        return str(self.state["run_id"])

    def book_state(self, book_id: str) -> dict[str, Any]:
        books = self.state.setdefault("books", {})
        return books.setdefault(book_id, {"queries": {}, "status": "pending"})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(self.state, file, ensure_ascii=False, indent=2)
                file.flush()
            temporary.replace(self.path)
        except OSError as exc:
            raise OutputError(f"Could not save checkpoint file {self.path}.") from exc


class RawResponseStore:
    def __init__(self, output_dir: Path) -> None:
        self.root = output_dir / "raw_search"

    @staticmethod
    def _safe(value: str) -> str:
        return _SAFE_ID.sub("_", value)

    def book_dir(self, run_id: str, book_id: str, query: str | None = None) -> Path:
        path = self.root / self._safe(run_id) / self._safe(book_id)
        if query is not None:
            query_id = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
            path = path / f"query_{query_id}"
        return path

    def save_page(
        self,
        run_id: str,
        book_id: str,
        page_index: int,
        payload: dict[str, Any],
        query: str | None = None,
    ) -> Path:
        path = self.book_dir(run_id, book_id, query) / f"page_{page_index:03d}.json"
        self._write_json(path, payload)
        return path

    def load_page(
        self, run_id: str, book_id: str, page_index: int, query: str | None = None
    ) -> dict[str, Any]:
        path = self.book_dir(run_id, book_id, query) / f"page_{page_index:03d}.json"
        try:
            with path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise OutputError(f"Could not load saved raw search response {path}.") from exc
        if not isinstance(payload, dict):
            raise OutputError(f"Saved raw search response is not a JSON object: {path}")
        return payload

    def save_work_details(
        self, run_id: str, book_id: str, work_id: str, payload: dict[str, Any]
    ) -> Path:
        path = self._details_dir(run_id, book_id) / f"work_{self._safe(work_id)}.json"
        self._write_json(path, payload)
        return path

    def load_work_details(
        self, run_id: str, book_id: str, work_id: str
    ) -> dict[str, Any] | None:
        path = self._details_dir(run_id, book_id) / f"work_{self._safe(work_id)}.json"
        return self._load_optional_json(path)

    def save_editions_page(
        self,
        run_id: str,
        book_id: str,
        work_id: str,
        page_index: int,
        payload: dict[str, Any],
    ) -> Path:
        path = (
            self._details_dir(run_id, book_id)
            / f"editions_{self._safe(work_id)}"
            / f"page_{page_index:03d}.json"
        )
        self._write_json(path, payload)
        return path

    def load_editions_page(
        self, run_id: str, book_id: str, work_id: str, page_index: int
    ) -> dict[str, Any] | None:
        path = (
            self._details_dir(run_id, book_id)
            / f"editions_{self._safe(work_id)}"
            / f"page_{page_index:03d}.json"
        )
        return self._load_optional_json(path)

    def save_metadata(
        self, run_id: str, book_id: str, metadata: dict[str, Any]
    ) -> None:
        self._write_json(self.book_dir(run_id, book_id) / "metadata.json", metadata)

    def _details_dir(self, run_id: str, book_id: str) -> Path:
        return self.book_dir(run_id, book_id) / "details"

    @staticmethod
    def _load_optional_json(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise OutputError(f"Could not load saved raw API response {path}.") from exc
        if not isinstance(payload, dict):
            raise OutputError(f"Saved raw API response is not a JSON object: {path}")
        return payload

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.flush()
            temporary.replace(path)
        except OSError as exc:
            raise OutputError(f"Could not save output file {path}.") from exc
