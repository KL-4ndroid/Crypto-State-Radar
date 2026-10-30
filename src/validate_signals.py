"""Forward validation for immutable radar signals.

Given a signal price/time and later candles, calculate return, MFE and MAE for
fixed horizons. Results are appended; the original signal must never be edited.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Iterable
import math

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
    signed_return_pct: float | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    boundary_gap_seconds: float = 0
    quality: str = "complete"
    methodology_version: str = "forward-v2"

    def to_dict(self): return asdict(self)


def validate(signal_id: str, signal_time: datetime, entry_price: float,
             candles: Iterable[dict], horizon: str, direction: str) -> Validation | None:
    hours = HORIZONS[horizon]
    end = signal_time + timedelta(hours=hours)
    if not math.isfinite(entry_price) or entry_price <= 0:
        return None
    interval = timedelta(minutes=5)
    # Only use complete candles wholly inside the forward window. In particular,
    # a candle opening at the horizon contains data from after the horizon.
    rows = sorted((r for r in candles if signal_time <= r["time"] and r["time"] + interval <= end), key=lambda r: r["time"])
    if (not rows or rows[0]["time"] - signal_time >= interval
            or end - (rows[-1]["time"] + interval) >= interval
            or any(b["time"] - a["time"] != interval for a, b in zip(rows, rows[1:]))):
        return None
    last = float(rows[-1]["close"])
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    if (any(not math.isfinite(v) or v<=0 for v in [last]+highs+lows)
            or any(float(r["low"])>float(r["close"]) or float(r["high"])<float(r["close"]) for r in rows)):
        return None
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
    coverage_end = rows[-1]["time"]+interval
    gap = (rows[0]["time"]-signal_time).total_seconds()+(end-coverage_end).total_seconds()
    return Validation(signal_id, horizon, datetime.now(timezone.utc).isoformat(),
                      entry_price, last, round(ret, 4), round(mfe, 4), round(mae, 4), outcome,
                      round(signed_ret,4),rows[0]["time"].isoformat(),coverage_end.isoformat(),gap,
                      "partial_boundaries" if gap else "complete")
