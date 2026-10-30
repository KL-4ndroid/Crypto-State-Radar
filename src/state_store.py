"""Durable state-store adapters for Radar lifecycle snapshots.

The state machine owns the snapshot schema. This module owns persistence.
Serverless workers can write directly to Upstash Redis, use an HTTP JSON bridge,
or fall back to a local JSON file for development.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import threading
import sqlite3
from pathlib import Path
from typing import Protocol

try:
    from .state_machine import SymbolState, decode_snapshot, encode_snapshot
except ImportError:  # Allow direct execution with PYTHONPATH=src
    from state_machine import SymbolState, decode_snapshot, encode_snapshot

DEFAULT_STATE_KEY = "crypto-state-radar:state:v1"
_FILE_PUBLICATION_LOCK = threading.Lock()


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

    def publish_snapshot(self, payload: dict) -> bool:
        # SQLite lock coordinates local processes as well as threads. It is not
        # the research ledger; it only serializes replacing this local read view.
        lock = sqlite3.connect(str(self.path)+".publish-lock",timeout=5)
        try:
            with _FILE_PUBLICATION_LOCK:
                lock.execute("BEGIN EXCLUSIVE")
                current = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
                if current.get("revision",0) > payload["revision"]:
                    return False
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
                temporary.replace(self.path)
        finally:
            lock.rollback()
            lock.close()
        self.states = decode_snapshot(payload)
        return True


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


class UpstashRestStateStore:
    """Persist one complete lifecycle snapshot directly through Upstash REST.

    Commands are sent in the request body rather than URL path segments so the
    snapshot size is not constrained by URL-length limits.
    """

    def __init__(self, base_url: str, token: str, key: str = DEFAULT_STATE_KEY, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.key = key
        self.timeout = timeout
        self.states = self._load()

    def _command(self, *parts: object) -> object:
        body = json.dumps([str(part) for part in parts], separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.base_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Upstash state command failed with HTTP {exc.code}") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError("Upstash state command failed")
        return payload.get("result")

    def _load(self) -> dict[str, SymbolState]:
        raw = self._command("GET", self.key)
        if raw is None:
            return {}
        payload = json.loads(str(raw))
        if not isinstance(payload, dict):
            raise RuntimeError("stored Radar state is not a JSON object")
        return decode_snapshot(payload)

    def _save(self) -> None:
        payload = json.dumps(encode_snapshot(self.states), ensure_ascii=False, separators=(",", ":"))
        result = self._command("SET", self.key, payload)
        if result != "OK":
            raise RuntimeError("Upstash state SET did not return OK")

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

    def publish_snapshot(self, payload: dict) -> bool:
        # Outbox replay can race newer commits; compare and write atomically.
        script = """local raw=redis.call('GET',KEYS[1]); local rev=0;
        if raw then local old=cjson.decode(raw); rev=tonumber(old.revision or 0) end;
        if rev>tonumber(ARGV[1]) then return 0 end;
        redis.call('SET',KEYS[1],ARGV[2]); return 1"""
        return self._command("EVAL",script,1,self.key,payload["revision"],json.dumps(payload,ensure_ascii=False,separators=(",",":"))) == 1


def state_store_from_env() -> StateStore:
    """Select durable persistence without coupling scanner logic to one host."""
    upstash_url = os.getenv("KV_REST_API_URL")
    upstash_token = os.getenv("KV_REST_API_TOKEN")
    if upstash_url and upstash_token:
        return UpstashRestStateStore(
            upstash_url,
            upstash_token,
            key=os.getenv("CSR_STATE_KEY", DEFAULT_STATE_KEY),
        )

    url = os.getenv("CSR_STATE_STORE_URL")
    if url:
        return HttpJsonStateStore(url, token=os.getenv("CSR_STATE_STORE_TOKEN"))
    return FileStateStore(os.getenv("CSR_STATE_FILE", "data/state/current.json"))
