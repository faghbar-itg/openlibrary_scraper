from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from openlibrary_scraper.utils.errors import OutputError


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
