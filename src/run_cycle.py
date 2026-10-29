"""Run one autonomous Crypto-State-Radar scan cycle.

Pipeline: Binance -> objective snapshot -> deterministic live features ->
V2.3 warning/V2.2 confirmation -> lifecycle transition -> durable state.
No LLM or OpenAI API is involved.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

try:
    from .live_collector import collect_market
    from .persistence import JsonlResearchStore
    from .radar_engine import classify
    from .state_machine import SymbolState, transition
    from .state_store import state_store_from_env
except ImportError:  # Allow direct execution: python src/run_cycle.py
    from live_collector import collect_market
    from persistence import JsonlResearchStore
    from radar_engine import classify
    from state_machine import SymbolState, transition
    from state_store import state_store_from_env

PRIORITY = {"EVAAUSDT", "ONUSDT", "PREDICTUSDT"}
SIGNAL_STATES = {"EARLY_WARNING", "PUMP_PRESSURE", "DUMP_PRESSURE", "LONG_READY", "SHORT_READY"}
NOTIFY_STATES = {"LONG_READY", "SHORT_READY", "TRIGGERED", "INVALIDATED", "EXPIRED"}


def _safe_float(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _features(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build only features knowable at scan time. Unknown values remain None."""
    return {
        "price": _safe_float(snapshot.get("price")),
        "funding_rate": _safe_float(snapshot.get("funding_rate")),
        "open_interest": _safe_float(snapshot.get("open_interest")),
        "oi_change_1h": (_safe_float(snapshot.get("oi_change_1h")) or 0.0) * 100.0 if snapshot.get("oi_change_1h") is not None else None,
        "oi_change_4h": (_safe_float(snapshot.get("oi_change_4h")) or 0.0) * 100.0 if snapshot.get("oi_change_4h") is not None else None,
        "oi_change_24h": (_safe_float(snapshot.get("oi_change_24h")) or 0.0) * 100.0 if snapshot.get("oi_change_24h") is not None else None,
        "price_change_1h": None,
        "price_change_4h": None,
        "funding_z": None,
        "funding_acceleration": None,
        "volume_z": None,
        "volatility_compression": None,
    }


def run(symbols: list[str] | None = None, root: str = "data/journal") -> dict[str, Any]:
    journal = JsonlResearchStore(root)
    lifecycle = state_store_from_env()
    previous_states = lifecycle.all()
    next_states = dict(previous_states)
    snapshots = collect_market(symbols=symbols)
    now = datetime.now(timezone.utc)
    summary = {"observed_at": now.isoformat(), "scanned": 0, "errors": 0, "signals": 0, "transitions": 0, "priority": [], "notifications": []}

    for snap in snapshots:
        symbol = snap.get("symbol", "UNKNOWN")
        if snap.get("error"):
            summary["errors"] += 1
            journal.append_observation(snap)
            continue

        summary["scanned"] += 1
        journal.append_observation(snap)
        features = _features(snap)
        decision = classify(symbol, features).to_dict()
        previous = previous_states.get(symbol, SymbolState(symbol=symbol))
        state, event = transition(previous, decision, now=now)
        next_states[symbol] = state

        # Journal one signal per lifecycle transition instead of repeating the
        # same signal every 15-minute scan while a state remains unchanged.
        if event and state.state in SIGNAL_STATES:
            signal = dict(decision)
            signal["cycle"] = state.cycle
            signal["lifecycle_state"] = state.state
            journal.append_signal(signal)
            summary["signals"] += 1

        if event:
            transition_record = dict(event)
            transition_record["price"] = snap.get("price")
            transition_record["reasons"] = decision.get("reasons", [])
            journal.append_transition(transition_record)
            summary["transitions"] += 1
            if state.state in NOTIFY_STATES or previous.state in NOTIFY_STATES:
                summary["notifications"].append(transition_record)

        if symbol in PRIORITY:
            summary["priority"].append({"symbol": symbol, "state": state.state, "pump_score": decision["pump_score"], "dump_score": decision["dump_score"], "price": snap.get("price"), "oi_1h": snap.get("oi_change_1h"), "oi_4h": snap.get("oi_change_4h"), "oi_24h": snap.get("oi_change_24h"), "funding": snap.get("funding_rate"), "ttl_minutes": decision.get("ttl_minutes"), "cycle": state.cycle})

    # Preserve symbols that were not returned this cycle and atomically replace
    # the durable snapshot once, after all transitions have been evaluated.
    lifecycle.put_all(next_states)
    return summary


if __name__ == "__main__":
    requested = [s.strip().upper() for s in os.getenv("CSR_SYMBOLS", "").split(",") if s.strip()] or None
    print(json.dumps(run(requested), ensure_ascii=False, indent=2))
