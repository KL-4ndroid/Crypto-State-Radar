"""Derive live V2.3 features from Binance public market history.

This module supplies the fields the radar engine expects without substituting
24h values for shorter horizons. Missing history stays None/neutral.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from live_collector import _get


def _ret(new: float, old: float) -> float | None:
    return None if old == 0 else (new / old - 1.0) * 100.0


def _z(value: float, history: list[float]) -> float | None:
    if len(history) < 5:
        return None
    sd = statistics.pstdev(history)
    return None if sd == 0 else (value - statistics.mean(history)) / sd


def derive_live_features(symbol: str) -> dict[str, Any]:
    # 5m klines: 7 days gives enough baseline for volume/volatility context.
    klines = _get("/fapi/v1/klines", {"symbol": symbol, "interval": "5m", "limit": 1500})
    if len(klines) < 289:
        return {}

    close = [float(x[4]) for x in klines]
    quote_volume = [float(x[7]) for x in klines]

    def price_change(bars: int) -> float | None:
        return _ret(close[-1], close[-1-bars]) if len(close) > bars else None

    # Compare latest 1h quote volume with rolling historical 1h blocks.
    block = 12
    one_hour_volumes = [sum(quote_volume[i:i+block]) for i in range(0, len(quote_volume)-block+1, block)]
    current_1h_volume = sum(quote_volume[-block:])
    volume_z = _z(current_1h_volume, one_hour_volumes[:-1][-168:])

    # Realized volatility from 5m log returns. Compression is baseline/current;
    # >1 means current 24h volatility is below its recent baseline.
    log_returns = [math.log(close[i] / close[i-1]) for i in range(1, len(close)) if close[i-1] > 0 and close[i] > 0]
    current_rv = statistics.pstdev(log_returns[-288:]) * math.sqrt(288) if len(log_returns) >= 288 else None
    daily_rv: list[float] = []
    for end in range(288, len(log_returns)+1, 288):
        window = log_returns[end-288:end]
        daily_rv.append(statistics.pstdev(window) * math.sqrt(288))
    baseline_rv = statistics.median(daily_rv[-7:-1]) if len(daily_rv) >= 2 else None
    compression = (baseline_rv / current_rv) if baseline_rv and current_rv else None

    # Funding history is sparse (normally settlement intervals), so calculate
    # acceleration on actual funding events rather than forward-filled 5m bars.
    funding_rows = _get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": 30})
    rates = [float(x["fundingRate"]) for x in funding_rows if x.get("fundingRate") is not None]
    funding_z = _z(rates[-1], rates[:-1][-21:]) if rates else None
    funding_acceleration = ((rates[-1] - rates[-2]) * 10000.0) if len(rates) >= 2 else None

    return {
        "price_change_1h": price_change(12),
        "price_change_4h": price_change(48),
        "price_change_24h": price_change(288),
        "volume_1h": current_1h_volume,
        "volume_z": volume_z,
        "funding_z": funding_z,
        "funding_acceleration": funding_acceleration,
        "realized_volatility_24h": current_rv,
        "volatility_compression": compression,
    }
