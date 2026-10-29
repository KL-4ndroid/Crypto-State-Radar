"""Portable persistence contract for observations, signals and validations.

JSONL is the zero-infrastructure development backend. Production can replace
this adapter with PostgreSQL without changing the radar engine contract.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


class JsonlResearchStore:
    def __init__(self, root: str | Path = "data/journal") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _append(self, name: str, row: dict[str, Any]) -> None:
        path = self.root / f"{name}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def append_observation(self, row: dict[str, Any]) -> None:
        self._append("observations", row)

    def append_signal(self, row: dict[str, Any]) -> None:
        # Append-only by design: signal-time knowledge must never be rewritten.
        self._append("signals", row)

    def append_transition(self, row: dict[str, Any]) -> None:
        self._append("transitions", row)

    def append_validation(self, row: dict[str, Any]) -> None:
        self._append("validations", row)

    def read(self, name: str) -> Iterable[dict[str, Any]]:
        path = self.root / f"{name}.jsonl"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
