"""Durable state-store adapters for Radar lifecycle snapshots.

The state machine owns the snapshot schema. This module owns persistence.
Production serverless workers can use an HTTP JSON store without depending on
an ephemeral filesystem; local development keeps using the JSON file adapter.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from state_machine import SymbolState, decode_snapshot, encode_snapshot


class StateStore(Protocol):
    def get(self, symbol: str) -> SymbolState: ...
    def put(self, state: SymbolState) -> None: ...
    def put_all(self, states: dict[str, SymbolState]) -> None: ...
    def all(self) -> dict[str, SymbolState]: ...


class FileStateStore:
    def __init__(self, path: str | Path = "data/state/current.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.states: dict[str, SymbolState] = {}
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.states = decode_snapshot(payload)

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(encode_snapshot(self.states), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, symbol: str) -> SymbolState:
        return self.states.get(symbol, SymbolState(symbol=symbol))

    def put(self, state: SymbolState) -> None:
        self.states[state.symbol] = state
        self._save()

    def put_all(self, states: dict[str, SymbolState]) -> None:
        self.states = dict(states)
        self._save()

    def all(self) -> dict[str, SymbolState]:
        return dict(self.states)


class HttpJsonStateStore:
    """Persist the complete versioned snapshot through a small HTTP JSON API."""

    def __init__(self, url: str, token: str | None = None, timeout: float = 10.0) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self.states = self._load()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _load(self) -> dict[str, SymbolState]:
        request = urllib.request.Request(self.url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise RuntimeError(f"state store GET failed with HTTP {exc.code}") from exc
        return decode_snapshot(payload)

    def _save(self) -> None:
        body = json.dumps(encode_snapshot(self.states), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, headers=self._headers(), method="PUT")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"state store PUT failed with HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"state store PUT failed with HTTP {exc.code}") from exc

    def get(self, symbol: str) -> SymbolState:
        return self.states.get(symbol, SymbolState(symbol=symbol))

    def put(self, state: SymbolState) -> None:
        self.states[state.symbol] = state
        self._save()

    def put_all(self, states: dict[str, SymbolState]) -> None:
        self.states = dict(states)
        self._save()

    def all(self) -> dict[str, SymbolState]:
        return dict(self.states)


def state_store_from_env() -> StateStore:
    """Select persistence without coupling scanner code to one vendor."""
    url = os.getenv("CSR_STATE_STORE_URL")
    if url:
        return HttpJsonStateStore(url, token=os.getenv("CSR_STATE_STORE_TOKEN"))
    return FileStateStore(os.getenv("CSR_STATE_FILE", "data/state/current.json"))
