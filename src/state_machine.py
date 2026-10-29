"""Temporal V2.2 state machine.

Turns independent radar decisions into a lifecycle with memory. The state store is
portable JSON so a later PostgreSQL adapter can preserve the same contract.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL = {"TRIGGERED", "INVALIDATED", "EXPIRED"}
READY = {"SHORT_READY", "LONG_READY"}


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


class JsonStateStore:
    def __init__(self, path: str | Path = "data/state/current.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.states: dict[str, SymbolState] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.states = {k: SymbolState(**v) for k, v in raw.items()}

    def get(self, symbol: str) -> SymbolState:
        return self.states.get(symbol, SymbolState(symbol=symbol))

    def put(self, state: SymbolState) -> None:
        self.states[state.symbol] = state
        self.path.write_text(json.dumps({k: asdict(v) for k, v in self.states.items()}, ensure_ascii=False, indent=2), encoding="utf-8")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def transition(previous: SymbolState, decision: dict[str, Any], now: datetime | None = None) -> tuple[SymbolState, dict[str, Any] | None]:
    now = now or datetime.now(timezone.utc)
    candidate = str(decision.get("state", "WATCH"))
    direction = str(decision.get("direction", "NEUTRAL"))
    features = decision.get("features") or {}
    price = features.get("price")
    oi = features.get("open_interest")

    new_state = candidate
    reason = "RADAR_CLASSIFICATION"

    # READY has a real TTL. If confirmation disappears after expiry, close the lifecycle.
    if previous.state in READY:
        expiry = _dt(previous.expires_at)
        if expiry and now >= expiry and candidate != previous.state:
            new_state, reason = "EXPIRED", "TTL_EXPIRED"
        elif previous.state == "SHORT_READY":
            p1 = float(features.get("price_change_1h") or 0)
            oi1 = float(features.get("oi_change_1h") or 0)
            if p1 > 0 and oi1 > 0:
                new_state, reason = "INVALIDATED", "PRICE_AND_OI_REEXPAND"
            elif candidate == "SHORT_READY" and price is not None and previous.ready_price is not None and float(price) < previous.ready_price:
                new_state, reason = "TRIGGERED", "BREAKDOWN_AFTER_SHORT_READY"
        elif previous.state == "LONG_READY":
            p1 = float(features.get("price_change_1h") or 0)
            oi1 = float(features.get("oi_change_1h") or 0)
            if p1 < 0 and oi1 < 0:
                new_state, reason = "INVALIDATED", "PRICE_AND_OI_FAIL"
            elif candidate == "LONG_READY" and price is not None and previous.ready_price is not None and float(price) > previous.ready_price:
                new_state, reason = "TRIGGERED", "BREAKOUT_AFTER_LONG_READY"

    # A terminal state can start a fresh cycle only when a new non-terminal setup appears.
    cycle = previous.cycle
    if previous.state in TERMINAL and new_state not in TERMINAL:
        cycle += 1
        reason = "NEW_CYCLE"

    entered = previous.entered_at
    if new_state != previous.state:
        entered = now.isoformat()

    state = SymbolState(
        symbol=previous.symbol,
        state=new_state,
        direction=direction if new_state not in TERMINAL else previous.direction,
        entered_at=entered or now.isoformat(),
        updated_at=now.isoformat(),
        expires_at=decision.get("expires_at") if new_state in READY else (previous.expires_at if new_state in TERMINAL else None),
        ready_price=float(price) if new_state in READY and price is not None else (previous.ready_price if previous.state in READY or new_state in TERMINAL else None),
        ready_oi=float(oi) if new_state in READY and oi is not None else (previous.ready_oi if previous.state in READY or new_state in TERMINAL else None),
        model_version=decision.get("model_version"),
        cycle=cycle,
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
