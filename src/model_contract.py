"""Pure, versioned features used by live scanning and historical replay."""
from __future__ import annotations
import math
import statistics
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

MODEL_VERSION = "V2.4.0-alpha"
CONTRACT_VERSION = 2
READY_SCORE = 55.0
COMPRESSION_THRESHOLD = 0.8
FUNDING_Z_MIN_HISTORY = 20


def model_manifest():
    sources = "".join((Path(__file__).with_name(name)).read_text(encoding="utf-8")
                      for name in ("model_contract.py","radar_engine.py","state_machine.py"))
    return {"model_version":MODEL_VERSION,"contract_version":CONTRACT_VERSION,
        "ready_score":READY_SCORE,"compression_threshold":COMPRESSION_THRESHOLD,
        "source_sha256":hashlib.sha256(sources.encode()).hexdigest()}


def number(value: Any) -> float | None:
    try:
        result = float(value) if value is not None else None
        return result if result is not None and math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def percent(new: float, old: float) -> float | None:
    return round((new / old - 1) * 100, 12) if old > 0 else None


def zscore(value: float, history: list[float], minimum: int = 5) -> float | None:
    if len(history) < minimum:
        return None
    sd = statistics.pstdev(history)
    return (value - statistics.mean(history)) / sd if sd > 0 else None


def snapshot_features(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = {k: number(snapshot.get(k)) for k in ("price", "open_interest", "funding_rate")}
    for horizon in (1, 4, 24):
        key = f"oi_change_{horizon}h"
        value = number(snapshot.get(key))
        result[key] = value * 100 if value is not None else None
    return result


def history_features(klines: list, funding: list, as_of: datetime) -> dict[str, Any]:
    cutoff = int(as_of.timestamp() * 1000)
    reasons: dict[str, str] = {}
    result = {k: None for k in ("price_change_1h", "price_change_4h", "price_change_24h", "volume_1h",
        "volume_z", "funding_z", "funding_acceleration", "realized_volatility_24h", "volatility_compression")}
    result.update(contract_version=CONTRACT_VERSION, data_as_of=as_of.isoformat(), candle_closed_at=None)
    rows = [r for r in klines if int(r[6]) < cutoff]
    valid = len(rows) >= 289 and all(int(b[0])-int(a[0]) == 300000 for a, b in zip(rows, rows[1:]))
    if rows and cutoff - int(rows[-1][6]) > 600000:
        valid = False
        reasons["price_history"] = "stale_history"
    close, volume = [number(r[4]) for r in rows], [number(r[7]) for r in rows]
    valid = valid and all(x is not None and x > 0 for x in close) and all(x is not None and x >= 0 for x in volume)
    if not valid:
        reasons.setdefault("price_history", "insufficient_or_invalid_contiguous_history")
    else:
        result["candle_closed_at"] = datetime.fromtimestamp(int(rows[-1][6])/1000, timezone.utc).isoformat()
        for hours in (1, 4, 24):
            result[f"price_change_{hours}h"] = percent(close[-1], close[-1-hours*12])
        result["volume_1h"] = sum(volume[-12:])
        volumes = [sum(volume[end-12:end]) for end in range(len(volume)-12, 11, -12)][:168]
        result["volume_z"] = zscore(result["volume_1h"], volumes, 24)
        if result["volume_z"] is None:
            reasons["volume_z"] = "insufficient_or_constant_history"
        returns = [math.log(b/a) for a, b in zip(close, close[1:])]
        rv = statistics.pstdev(returns[-288:]) * math.sqrt(288)
        baseline = [statistics.pstdev(returns[end-288:end])*math.sqrt(288)
                    for end in range(len(returns)-288, 287, -288)][:7]
        result["realized_volatility_24h"] = rv
        if len(baseline) >= 3 and statistics.median(baseline) > 0:
            result["volatility_compression"] = rv / statistics.median(baseline)
        else:
            reasons["volatility_compression"] = "insufficient_or_zero_baseline"
    start = cutoff - 7*86400000
    settlements = sorted((int(r["fundingTime"]), number(r.get("fundingRate"))) for r in funding
                         if start <= int(r["fundingTime"]) <= cutoff)
    if (any(v is None for _, v in settlements) or len({t for t, _ in settlements}) != len(settlements)
            or not settlements or cutoff - settlements[-1][0] > 86400000):
        reasons["funding_z"] = reasons["funding_acceleration"] = "missing_invalid_or_stale_funding"
    else:
        rates = [v for _, v in settlements]
        # A seven-day 8h-settlement window normally has 21 rows, not 22.
        # Require temporal coverage as well as count (dense new listings must
        # not masquerade as a seven-day baseline).
        covered = cutoff-settlements[0][0] >= 6*86400000
        result["funding_z"] = zscore(rates[-1], rates[:-1], FUNDING_Z_MIN_HISTORY) if covered else None
        # Basis-point delta per settlement, not a fabricated 24h derivative.
        result["funding_acceleration"] = (rates[-1]-rates[-2])*10000 if len(rates) >= 2 else None
        if result["funding_z"] is None:
            reasons["funding_z"] = "insufficient_or_constant_settlements"
        if result["funding_acceleration"] is None:
            reasons["funding_acceleration"] = "insufficient_settlements"
    result["missing_reasons"] = reasons
    return result
