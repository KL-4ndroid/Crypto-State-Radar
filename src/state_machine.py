"""Temporal V2.2 state machine.

Turns independent radar decisions into a lifecycle with memory. The state store
uses a versioned portable snapshot contract so the same state can later live in
SQLite/PostgreSQL without changing transition semantics.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL = {"TRIGGERED", "INVALIDATED", "EXPIRED"}
READY = {"SHORT_READY", "LONG_READY"}
STATE_SCHEMA_VERSION = 1


@dataclass
class SymbolState:
    symbol: str
    state: str = "WATCH"
    direction: str = "NEUTRAL"
    entered_at: str | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    ready_price: float | None = None
    ready_oi: float | None = None
    model_version: str | None = None
    cycle: int = 0
    peak_price: float | None = None
    peak_oi: float | None = None
    trough_price: float | None = None
    trough_oi: float | None = None
    last_transition: str | None = None


def state_to_dict(state: SymbolState) -> dict[str, Any]:
    return asdict(state)


def state_from_dict(payload: dict[str, Any]) -> SymbolState:
    # Ignore future fields so older workers can still read a newer snapshot.
    allowed = SymbolState.__dataclass_fields__.keys()
    return SymbolState(**{k: v for k, v in payload.items() if k in allowed})


def encode_snapshot(states: dict[str, SymbolState]) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "states": {symbol: state_to_dict(state) for symbol, state in states.items()},
    }


def decode_snapshot(payload: dict[str, Any]) -> dict[str, SymbolState]:
    # Backward compatibility: the original file format was simply {symbol: state}.
    raw_states = payload.get("states") if "states" in payload else payload
    if not isinstance(raw_states, dict):
        raise ValueError("state snapshot must contain an object of symbol states")
    return {symbol: state_from_dict(value) for symbol, value in raw_states.items() if isinstance(value, dict)}


class JsonStateStore:
    """Local adapter for the portable state snapshot contract.

    Suitable for development and persistent hosts. Serverless runtimes should
    load/save the same snapshot through a durable adapter rather than relying on
    the function filesystem.
    """

    def __init__(self, path: str | Path = "data/state/current.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.states: dict[str, SymbolState] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.states = decode_snapshot(raw)

    def get(self, symbol: str) -> SymbolState:
        return self.states.get(symbol, SymbolState(symbol=symbol))

    def put(self, state: SymbolState) -> None:
        self.states[state.symbol] = state
        self.path.write_text(
            json.dumps(encode_snapshot(self.states), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _max_seen(previous: float | None, current: float | None) -> float | None:
    if current is None:
        return previous
    return current if previous is None else max(previous, current)


def _min_seen(previous: float | None, current: float | None) -> float | None:
    if current is None:
        return previous
    return current if previous is None else min(previous, current)


def transition(previous: SymbolState, decision: dict[str, Any], now: datetime | None = None) -> tuple[SymbolState, dict[str, Any] | None]:
    now = now or datetime.now(timezone.utc)
    candidate = str(decision.get("state", "WATCH"))
    direction = str(decision.get("direction", "NEUTRAL"))
    features = decision.get("features") or {}
    price = _number(features.get("price"))
    oi = _number(features.get("open_interest"))

    new_state = candidate
    reason = "RADAR_CLASSIFICATION"

    if previous.state in READY:
        expiry = _dt(previous.expires_at)
        if expiry and now >= expiry and candidate != previous.state:
            new_state, reason = "EXPIRED", "TTL_EXPIRED"
        elif previous.state == "SHORT_READY":
            p1 = float(features.get("price_change_1h") or 0)
            oi1 = float(features.get("oi_change_1h") or 0)
            if p1 > 0 and oi1 > 0:
                new_state, reason = "INVALIDATED", "PRICE_AND_OI_REEXPAND"
            elif candidate == "SHORT_READY" and price is not None and previous.ready_price is not None and price < previous.ready_price:
                new_state, reason = "TRIGGERED", "BREAKDOWN_AFTER_SHORT_READY"
        elif previous.state == "LONG_READY":
            p1 = float(features.get("price_change_1h") or 0)
            oi1 = float(features.get("oi_change_1h") or 0)
            if p1 < 0 and oi1 < 0:
                new_state, reason = "INVALIDATED", "PRICE_AND_OI_FAIL"
            elif candidate == "LONG_READY" and price is not None and previous.ready_price is not None and price > previous.ready_price:
                new_state, reason = "TRIGGERED", "BREAKOUT_AFTER_LONG_READY"

    cycle = previous.cycle
    fresh_cycle = previous.state in TERMINAL and new_state not in TERMINAL
    if fresh_cycle:
        cycle += 1
        reason = "NEW_CYCLE"

    entered = previous.entered_at
    if new_state != previous.state:
        entered = now.isoformat()

    peak_price = price if fresh_cycle else _max_seen(previous.peak_price, price)
    peak_oi = oi if fresh_cycle else _max_seen(previous.peak_oi, oi)
    trough_price = price if fresh_cycle else _min_seen(previous.trough_price, price)
    trough_oi = oi if fresh_cycle else _min_seen(previous.trough_oi, oi)
    transition_name = previous.last_transition
    if new_state != previous.state:
        transition_name = f"{previous.state}->{new_state}"

    state = SymbolState(
        symbol=previous.symbol,
        state=new_state,
        direction=direction if new_state not in TERMINAL else previous.direction,
        entered_at=entered or now.isoformat(),
        updated_at=now.isoformat(),
        expires_at=decision.get("expires_at") if new_state in READY else (previous.expires_at if new_state in TERMINAL else None),
        ready_price=price if new_state in READY and price is not None else (previous.ready_price if previous.state in READY or new_state in TERMINAL else None),
        ready_oi=oi if new_state in READY and oi is not None else (previous.ready_oi if previous.state in READY or new_state in TERMINAL else None),
        model_version=decision.get("model_version"),
        cycle=cycle,
        peak_price=peak_price,
        peak_oi=peak_oi,
        trough_price=trough_price,
        trough_oi=trough_oi,
        last_transition=transition_name,
    )

    event = None
    if state.state != previous.state:
        event = {
            "symbol": state.symbol,
            "at": now.isoformat(),
            "from_state": previous.state,
            "to_state": state.state,
            "direction": state.direction,
            "reason": reason,
            "cycle": state.cycle,
            "model_version": state.model_version,
        }
    return state, event
