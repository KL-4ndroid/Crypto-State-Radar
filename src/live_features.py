"""Network adapter for the pure live/replay feature contract."""
from __future__ import annotations
import time
from datetime import datetime, timezone
try:
    from .live_collector import _get
    from .model_contract import history_features, percent as _ret, zscore as _z
except ImportError:
    from live_collector import _get
    from model_contract import history_features, percent as _ret, zscore as _z


def derive_live_features(symbol: str) -> dict:
    as_of = datetime.fromtimestamp(time.time(), timezone.utc)
    klines = _get("/fapi/v1/klines", {"symbol": symbol, "interval": "5m", "limit": 1500})
    funding_error = None
    try:
        funding = _get("/fapi/v1/fundingRate", {"symbol": symbol,
            "startTime": int(as_of.timestamp()*1000)-7*86400000,
            "endTime": int(as_of.timestamp()*1000), "limit": 1000})
    except Exception as exc:
        funding, funding_error = [], type(exc).__name__
    result = history_features(klines, funding, as_of)
    if funding_error:
        result["missing_reasons"]["funding_api"] = funding_error
    return result
