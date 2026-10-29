"""Run one autonomous Crypto-State-Radar scan cycle.

Pipeline: Binance -> objective snapshot -> deterministic live features ->
V2.3 warning/V2.2 confirmation -> immutable journal -> state transitions.
No LLM or OpenAI API is involved.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from live_collector import collect_market
from persistence import JsonlResearchStore
from radar_engine import classify

PRIORITY = {"EVAAUSDT", "ONUSDT", "PREDICTUSDT"}
SIGNAL_STATES = {"EARLY_WARNING", "PUMP_PRESSURE", "DUMP_PRESSURE", "LONG_READY", "SHORT_READY"}
NOTIFY_STATES = {"LONG_READY", "SHORT_READY", "TRIGGERED", "INVALIDATED", "EXPIRED"}


def _safe_float(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _load_previous_states(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _features(snapshot: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Build only features knowable at scan time. Unknown values remain None."""
    current_price = _safe_float(snapshot.get("price"))
    previous_price = _safe_float((previous or {}).get("price"))
    price_change_1h = None
    # Until a true one-hour price observation exists in the journal, do not fake it.
    if previous and previous.get("age_minutes") == 60 and current_price and previous_price:
        price_change_1h = (current_price / previous_price - 1.0) * 100.0

    return {
        "price": current_price,
        "funding_rate": _safe_float(snapshot.get("funding_rate")),
        "open_interest": _safe_float(snapshot.get("open_interest")),
        "oi_change_1h": (_safe_float(snapshot.get("oi_change_1h")) or 0.0) * 100.0 if snapshot.get("oi_change_1h") is not None else None,
        "oi_change_4h": (_safe_float(snapshot.get("oi_change_4h")) or 0.0) * 100.0 if snapshot.get("oi_change_4h") is not None else None,
        "oi_change_24h": (_safe_float(snapshot.get("oi_change_24h")) or 0.0) * 100.0 if snapshot.get("oi_change_24h") is not None else None,
        "price_change_1h": price_change_1h,
        "price_change_4h": None,
        "funding_z": None,
        "funding_acceleration": None,
        "volume_z": None,
        "volatility_compression": None,
    }


def run(symbols: list[str] | None = None, root: str = "data/journal") -> dict[str, Any]:
    store = JsonlResearchStore(root)
    state_path = Path(root) / "latest_states.json"
    previous_states = _load_previous_states(state_path)
    snapshots = collect_market(symbols=symbols)
    now = datetime.now(timezone.utc).isoformat()
    next_states: dict[str, dict[str, Any]] = {}
    summary = {"observed_at": now, "scanned": 0, "errors": 0, "signals": 0, "transitions": 0, "priority": [], "notifications": []}

    for snap in snapshots:
        symbol = snap.get("symbol", "UNKNOWN")
        if snap.get("error"):
            summary["errors"] += 1
            store.append_observation(snap)
            continue

        summary["scanned"] += 1
        store.append_observation(snap)
        previous = previous_states.get(symbol)
        decision = classify(symbol, _features(snap, previous)).to_dict()
        state = decision["state"]
        next_states[symbol] = {"state": state, "direction": decision["direction"], "price": snap.get("price"), "observed_at": decision["observed_at"], "expires_at": decision.get("expires_at")}

        if state in SIGNAL_STATES:
            store.append_signal(decision)
            summary["signals"] += 1

        old_state = (previous or {}).get("state")
        if old_state and old_state != state:
            transition = {"symbol": symbol, "observed_at": decision["observed_at"], "from_state": old_state, "to_state": state, "direction": decision["direction"], "model_version": decision["model_version"], "price": snap.get("price"), "reasons": decision.get("reasons", [])}
            store.append_transition(transition)
            summary["transitions"] += 1
            if state in NOTIFY_STATES or old_state in NOTIFY_STATES:
                summary["notifications"].append(transition)

        if symbol in PRIORITY:
            summary["priority"].append({"symbol": symbol, "state": state, "pump_score": decision["pump_score"], "dump_score": decision["dump_score"], "price": snap.get("price"), "oi_1h": snap.get("oi_change_1h"), "oi_4h": snap.get("oi_change_4h"), "oi_24h": snap.get("oi_change_24h"), "funding": snap.get("funding_rate"), "ttl_minutes": decision.get("ttl_minutes")})

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(next_states, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    requested = [s.strip().upper() for s in os.getenv("CSR_SYMBOLS", "").split(",") if s.strip()] or None
    print(json.dumps(run(requested), ensure_ascii=False, indent=2))
