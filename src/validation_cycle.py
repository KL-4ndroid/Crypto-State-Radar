"""Autonomous forward validation cycle for immutable radar signals.

Finds due signal/horizon pairs, fetches Binance 5m candles, calculates outcome,
and appends validation rows without rewriting the original signal.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from live_collector import _get
from persistence import JsonlResearchStore
from validate_signals import HORIZONS, validate


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def signal_id(signal: dict[str, Any]) -> str:
    existing = signal.get("signal_id")
    if existing:
        return str(existing)
    raw = "|".join([
        str(signal.get("symbol", "")),
        str(signal.get("observed_at", "")),
        str(signal.get("model_version", "")),
        str(signal.get("state", "")),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _candles(symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while cursor <= end_ms:
        batch = _get("/fapi/v1/klines", {
            "symbol": symbol,
            "interval": "5m",
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1500,
        })
        if not batch:
            break
        for r in batch:
            rows.append({
                "time": datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
                "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]),
            })
        nxt = int(batch[-1][0]) + 300_000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 1500:
            break
    return rows


def run_validation_cycle(store: JsonlResearchStore | None = None,
                         now: datetime | None = None) -> dict[str, Any]:
    store = store or JsonlResearchStore()
    now = now or datetime.now(timezone.utc)
    signals = list(store.read("signals"))
    validations = list(store.read("validations"))
    completed = {(str(v.get("signal_id")), str(v.get("horizon"))) for v in validations}
    written: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for signal in signals:
        sid = signal_id(signal)
        symbol = str(signal.get("symbol", ""))
        observed = signal.get("observed_at")
        entry = signal.get("entry_price")
        direction = str(signal.get("direction", "NEUTRAL"))
        if not symbol or not observed or entry is None:
            continue
        start = _parse_time(str(observed))
        for horizon, hours in HORIZONS.items():
            key = (sid, horizon)
            due = start + timedelta(hours=hours)
            if key in completed or now < due + timedelta(minutes=10):
                continue
            try:
                candles = _candles(symbol, start, due)
                result = validate(sid, start, float(entry), candles, horizon, direction)
                if result is None:
                    continue
                row = result.to_dict()
                row.update({
                    "symbol": symbol,
                    "signal_time": start.isoformat(),
                    "signal_state": signal.get("state"),
                    "direction": direction,
                    "model_version": signal.get("model_version"),
                    "pump_score": signal.get("pump_score"),
                    "dump_score": signal.get("dump_score"),
                })
                store.append_validation(row)
                completed.add(key)
                written.append(row)
            except Exception as exc:
                errors.append({"symbol": symbol, "horizon": horizon, "error": str(exc)})

    return {
        "checked_at": now.isoformat(),
        "signals": len(signals),
        "new_validations": len(written),
        "validated": written,
        "errors": errors[:50],
    }
