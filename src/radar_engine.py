"""Autonomous V2.2/V2.3 radar engine.

Pure deterministic rules: no LLM/API dependency. Input is a feature snapshot,
output is an immutable candidate decision suitable for Signal Journal storage.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

MODEL_VERSION = "V2.3.0-alpha"
READY_SCORE = 55.0


@dataclass(frozen=True)
class RadarDecision:
    symbol: str
    observed_at: str
    model_version: str
    pump_score: float
    dump_score: float
    state: str
    direction: str
    ttl_minutes: int | None
    expires_at: str | None
    reasons: tuple[str, ...]
    invalidation: str
    features: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reasons"] = list(self.reasons)
        return d


def _clip(v: float) -> float:
    return round(max(0.0, min(100.0, v)), 2)


def score(features: dict[str, Any]) -> tuple[float, float, list[str], list[str]]:
    """Score early-warning pressure. Missing fields are neutral, never fabricated."""
    oi1 = float(features.get("oi_change_1h") or 0)
    oi4 = float(features.get("oi_change_4h") or 0)
    oi24 = float(features.get("oi_change_24h") or 0)
    p1 = float(features.get("price_change_1h") or 0)
    p4 = float(features.get("price_change_4h") or 0)
    funding_z = float(features.get("funding_z") or 0)
    funding_acc = float(features.get("funding_acceleration") or 0)
    vol_z = float(features.get("volume_z") or 0)
    compression = float(features.get("volatility_compression") or 0)

    oi_acc = oi1 - (oi24 / 24.0)
    pump = dump = 0.0
    pr: list[str] = []
    dr: list[str] = []

    if oi_acc > 1: pump += min(24, oi_acc * 5); dump += min(24, oi_acc * 5)
    if vol_z > 1.5: pump += min(18, vol_z * 5); dump += min(18, vol_z * 5)
    if compression > 0.5: pump += 10; dump += 10

    if oi1 > 0 and p1 > 0:
        pump += min(28, p1 * 4 + oi1 * 1.5); pr.append("PRICE_UP_OI_UP")
    if oi1 > 0 and p1 < 0:
        dump += min(30, abs(p1) * 4 + oi1 * 1.5); dr.append("PRICE_DOWN_OI_UP")

    if funding_z < -1.5 and p1 >= 0:
        pump += min(25, abs(funding_z) * 7); pr.append("SHORT_CROWDING_RESISTED")
    if funding_z > 1.5 and p1 <= 0:
        dump += min(25, funding_z * 7); dr.append("LONG_CROWDING_RESISTED")
    if funding_acc < 0 and p1 >= 0: pump += min(10, abs(funding_acc) * 4)
    if funding_acc > 0 and p1 <= 0: dump += min(10, funding_acc * 4)

    if oi_acc > 1: pr.append("OI_ACCELERATION"); dr.append("OI_ACCELERATION")
    if vol_z > 1.5: pr.append("VOLUME_EXPANSION"); dr.append("VOLUME_EXPANSION")
    return _clip(pump), _clip(dump), pr, dr


def classify(symbol: str, features: dict[str, Any], now: datetime | None = None) -> RadarDecision:
    now = now or datetime.now(timezone.utc)
    pump, dump, pr, dr = score(features)
    oi1 = float(features.get("oi_change_1h") or 0)
    oi4 = float(features.get("oi_change_4h") or 0)
    p1 = float(features.get("price_change_1h") or 0)
    p4 = float(features.get("price_change_4h") or 0)

    state, direction, ttl, reasons = "WATCH", "NEUTRAL", None, []
    invalidation = "Score falls below watch threshold or structure reverses."

    # V2.3 score identifies pressure; V2.2 execution requires structure confirmation.
    # Once both price and OI confirm on 1h + 4h, a moderate-high pressure score is
    # sufficient for READY. Requiring 70 here made confirmed breakdowns remain only
    # EARLY_WARNING, which contradicted the execution-state design.
    short_confirmed = p1 < 0 and p4 < 0 and oi1 < 0 and oi4 < 0
    long_confirmed = p1 > 0 and p4 > 0 and oi1 > 0 and oi4 > 0

    if dump >= READY_SCORE and short_confirmed:
        state, direction, ttl, reasons = "SHORT_READY", "SHORT", 240, dr + ["PRICE_CONFIRMATION", "OI_RETREAT"]
        invalidation = "Price reclaims breakdown structure while OI 1h/4h re-expand."
    elif pump >= READY_SCORE and long_confirmed:
        state, direction, ttl, reasons = "LONG_READY", "LONG", 240, pr + ["PRICE_CONFIRMATION", "OI_EXPANSION"]
        invalidation = "Price loses breakout structure while OI expansion fails."
    elif dump >= 60:
        state, direction, reasons = "DUMP_PRESSURE", "SHORT_WATCH", dr
    elif pump >= 60:
        state, direction, reasons = "PUMP_PRESSURE", "LONG_WATCH", pr
    elif max(pump, dump) >= 40:
        state, direction = "EARLY_WARNING", "LONG_WATCH" if pump > dump else "SHORT_WATCH"
        reasons = pr if pump > dump else dr

    expires = (now + timedelta(minutes=ttl)).isoformat() if ttl else None
    return RadarDecision(symbol, now.isoformat(), MODEL_VERSION, pump, dump, state,
                         direction, ttl, expires, tuple(dict.fromkeys(reasons)),
                         invalidation, dict(features))
