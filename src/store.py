"""Durable research storage.

SQLite is the zero-service production-like backend for workers and local research.
The Store protocol keeps runtime code portable so PostgreSQL can replace it later.
Signals are immutable; validations are idempotent by (signal_id, horizon).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Protocol


class ResearchStore(Protocol):
    def append_observation(self, row: dict[str, Any]) -> None: ...
    def append_signal(self, row: dict[str, Any]) -> None: ...
    def append_transition(self, row: dict[str, Any]) -> None: ...
    def append_validation(self, row: dict[str, Any]) -> None: ...
    def read(self, name: str) -> Iterable[dict[str, Any]]: ...


class SqliteResearchStore:
    TABLES = {"observations", "signals", "transitions", "validations"}

    def __init__(self, path: str | Path = "data/research.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._init()

    def _init(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          payload TEXT NOT NULL,
          UNIQUE(symbol, observed_at)
        );
        CREATE TABLE IF NOT EXISTS signals (
          signal_id TEXT PRIMARY KEY,
          symbol TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          model_version TEXT,
          state TEXT,
          direction TEXT,
          entry_price REAL,
          payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals(symbol, observed_at);
        CREATE TABLE IF NOT EXISTS transitions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol TEXT NOT NULL,
          at TEXT NOT NULL,
          from_state TEXT,
          to_state TEXT,
          cycle INTEGER,
          payload TEXT NOT NULL,
          UNIQUE(symbol, at, from_state, to_state, cycle)
        );
        CREATE TABLE IF NOT EXISTS validations (
          signal_id TEXT NOT NULL,
          horizon TEXT NOT NULL,
          validated_at TEXT NOT NULL,
          outcome TEXT,
          payload TEXT NOT NULL,
          PRIMARY KEY(signal_id, horizon)
        );
        """)
        self.db.commit()

    @staticmethod
    def _json(row: dict[str, Any]) -> str:
        return json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def append_observation(self, row: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO observations(symbol,observed_at,payload) VALUES(?,?,?)",
            (row["symbol"], row["observed_at"], self._json(row)),
        ); self.db.commit()

    def append_signal(self, row: dict[str, Any]) -> None:
        # INSERT OR IGNORE enforces immutable signal-time knowledge.
        self.db.execute(
            "INSERT OR IGNORE INTO signals(signal_id,symbol,observed_at,model_version,state,direction,entry_price,payload) VALUES(?,?,?,?,?,?,?,?)",
            (row["signal_id"], row["symbol"], row["observed_at"], row.get("model_version"), row.get("state"), row.get("direction"), row.get("entry_price"), self._json(row)),
        ); self.db.commit()

    def append_transition(self, row: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO transitions(symbol,at,from_state,to_state,cycle,payload) VALUES(?,?,?,?,?,?)",
            (row["symbol"], row["at"], row.get("from_state"), row.get("to_state"), row.get("cycle"), self._json(row)),
        ); self.db.commit()

    def append_validation(self, row: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO validations(signal_id,horizon,validated_at,outcome,payload) VALUES(?,?,?,?,?)",
            (row["signal_id"], row["horizon"], row["validated_at"], row.get("outcome"), self._json(row)),
        ); self.db.commit()

    def read(self, name: str) -> list[dict[str, Any]]:
        if name not in self.TABLES:
            raise ValueError(f"unknown table: {name}")
        return [json.loads(r[0]) for r in self.db.execute(f"SELECT payload FROM {name} ORDER BY rowid")]

    def close(self) -> None:
        self.db.close()
