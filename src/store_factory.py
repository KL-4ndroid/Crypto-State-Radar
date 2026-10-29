"""Choose persistence backend without coupling the radar to deployment vendor."""
from __future__ import annotations

import os

from persistence import JsonlResearchStore
from store import SqliteResearchStore


def create_store():
    backend = os.getenv("CSR_STORE", "sqlite").lower().strip()
    if backend == "jsonl":
        return JsonlResearchStore(os.getenv("CSR_JOURNAL_DIR", "data/journal"))
    if backend == "sqlite":
        return SqliteResearchStore(os.getenv("CSR_SQLITE_PATH", "data/research.sqlite3"))
    raise ValueError(f"Unsupported CSR_STORE={backend!r}; expected sqlite or jsonl")
