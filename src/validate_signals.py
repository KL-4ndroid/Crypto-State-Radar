"""Forward validation for immutable radar signals.

Given a signal price/time and later candles, calculate return, MFE and MAE for
fixed horizons. Results are appended; the original signal must never be edited.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

HORIZONS = {"1h": 1, "3h": 3, "6h": 6, "12h": 12, "24h": 24, "3d": 72, "7d": 168}

@dataclass(frozen=True)
class Validation:
    signal_id: str
    horizon: str
    validated_at: str
    entry_price: float
    end_price: float
    return_pct: float
    mfe_pct: float
    mae_pct: float
    outcome: str

    def to_dict(self): return asdict(self)


def validate(signal_id: str, signal_time: datetime, entry_price: float,
             candles: Iterable[dict], horizon: str, direction: str) -> Validation | None:
    hours = HORIZONS[horizon]
    end = signal_time + timedelta(hours=hours)
    rows = [r for r in candles if signal_time <= r["time"] <= end]
    if not rows or rows[-1]["time"] < end - timedelta(minutes=10):
        return None
    last = float(rows[-1]["close"])
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    ret = (last / entry_price - 1) * 100
    mfe_long = (max(highs) / entry_price - 1) * 100
    mae_long = (min(lows) / entry_price - 1) * 100
    if direction in {"SHORT", "SHORT_WATCH"}:
        signed_ret, mfe, mae = -ret, -mae_long, -mfe_long
    else:
        signed_ret, mfe, mae = ret, mfe_long, mae_long
    if signed_ret >= 30: outcome = "STRONG_SUCCESS"
    elif signed_ret >= 10: outcome = "SUCCESS"
    elif signed_ret > -5: outcome = "NEUTRAL"
    else: outcome = "FAILURE"
    return Validation(signal_id, horizon, datetime.now(timezone.utc).isoformat(),
                      entry_price, last, round(ret, 4), round(mfe, 4), round(mae, 4), outcome)
