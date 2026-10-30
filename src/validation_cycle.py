"""Autonomous forward validation cycle for immutable radar signals.

Finds due signal/horizon pairs, fetches Binance 5m candles, calculates outcome,
and appends validation rows without rewriting the original signal.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from .live_collector import _get
    from .persistence import JsonlResearchStore
    from .validate_signals import HORIZONS, validate
except ImportError:
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
    signals = [] if hasattr(store,"due_jobs") else list(store.read("signals"))
    validations = [] if hasattr(store,"due_jobs") else list(store.read("validations"))
    completed = {(str(v.get("signal_id")), str(v.get("horizon"))) for v in validations}
    written: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    # Durable jobs are selected by indexed due time; legacy JSONL remains a local adapter.
    if hasattr(store,"claim_due_jobs"):
        jobs=store.claim_due_jobs(now,limit=20)
    elif hasattr(store,"due_jobs"):
        jobs=[(sid,h,None) for sid,h in store.due_jobs(now,limit=20)]
    else:
        jobs=[(signal_id(signal),h,None) for signal in signals for h in HORIZONS]
    lookup = {signal_id(signal): signal for signal in signals}
    for sid, horizon, lease_token in jobs:
        signal = store.signal(sid) if hasattr(store, "signal") else lookup.get(sid)
        if not signal:
            if hasattr(store,"finish_validation"):
                store.finish_validation(sid,horizon,error="missing_signal",lease_token=lease_token)
            continue
        symbol = str(signal.get("symbol", ""))
        direction = str(signal.get("direction", "NEUTRAL"))
        try:
            start = _parse_time(str(signal["observed_at"]))
            entry = float(signal["entry_price"])
            due = start + timedelta(hours=HORIZONS[horizon])
            if (sid,horizon) in completed or now < due+timedelta(minutes=10):
                continue
            candles = _candles(symbol,start,due)
            result = validate(sid,start,entry,candles,horizon,direction)
            if result is None:
                if hasattr(store,"finish_validation"):
                    store.finish_validation(sid,horizon,error="insufficient_or_invalid_candles",lease_token=lease_token)
                continue
            row = result.to_dict()
            row.update(symbol=symbol,signal_time=start.isoformat(),signal_state=signal.get("state"),
                direction=direction,model_version=signal.get("model_version"),pump_score=signal.get("pump_score"),dump_score=signal.get("dump_score"))
            if hasattr(store,"finish_validation"):
                if not store.finish_validation(sid,horizon,row=row,lease_token=lease_token):
                    continue
            else:
                store.append_validation(row)
            completed.add((sid,horizon))
            written.append(row)
        except Exception as exc:
            if hasattr(store,"finish_validation"):
                store.finish_validation(sid,horizon,error=type(exc).__name__,lease_token=lease_token)
            errors.append({"symbol":symbol,"horizon":horizon,"error":type(exc).__name__})

    return {
        "checked_at": now.isoformat(),
        "signals": len({sid for sid,_,_ in jobs}),
        "checked_jobs": len(jobs),
        "new_validations": len(written),
        "validated": written,
        "errors": errors[:50],
    }
