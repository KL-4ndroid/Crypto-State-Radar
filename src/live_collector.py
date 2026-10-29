"""Binance USDT-M live collector for Crypto-State-Radar.

Collects objective market observations only. No trading decisions are made here.
OI changes are stored as decimal ratios (0.05 == +5%); conversion to percentage
points happens at the feature boundary in run_cycle.py.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

DEFAULT_BASE_URL = "https://fapi.binance.com"
BASE_URL = os.getenv("CSR_BINANCE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


class MarketDataUnavailable(RuntimeError):
    """Raised when the configured market-data origin cannot serve the runner."""


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Crypto-State-Radar/0.2"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 451:
            raise MarketDataUnavailable(
                "Binance Futures returned HTTP 451 for this runner location. "
                "Run the collector from a Binance-supported region or set "
                "CSR_BINANCE_BASE_URL to an approved market-data gateway you control."
            ) from exc
        raise


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    observed_at: str
    price: float
    quote_volume_24h: float
    funding_rate: float | None
    open_interest: float | None
    oi_change_1h: float | None
    oi_change_4h: float | None
    oi_change_24h: float | None


def active_usdt_perpetuals() -> list[str]:
    info = _get("/fapi/v1/exchangeInfo")
    return sorted(
        item["symbol"]
        for item in info["symbols"]
        if item.get("contractType") == "PERPETUAL"
        and item.get("quoteAsset") == "USDT"
        and item.get("status") == "TRADING"
    )


def _pct_change(current: float, previous: float) -> float | None:
    """Return decimal ratio, e.g. 110 vs 100 -> 0.10."""
    if previous == 0:
        return None
    return current / previous - 1.0


def _oi_history(symbol: str) -> list[dict[str, Any]]:
    return _get("/futures/data/openInterestHist", {"symbol": symbol, "period": "5m", "limit": 500})


def collect_symbol(symbol: str, ticker: dict[str, Any] | None = None, funding: dict[str, Any] | None = None) -> MarketSnapshot:
    ticker = ticker or _get("/fapi/v1/ticker/24hr", {"symbol": symbol})
    funding = funding or _get("/fapi/v1/premiumIndex", {"symbol": symbol})
    history = _oi_history(symbol)
    current_oi = float(history[-1]["sumOpenInterestValue"]) if history else None

    def oi_change(bars: int) -> float | None:
        if current_oi is None or len(history) <= bars:
            return None
        return _pct_change(current_oi, float(history[-1 - bars]["sumOpenInterestValue"]))

    return MarketSnapshot(
        symbol=symbol,
        observed_at=datetime.now(timezone.utc).isoformat(),
        price=float(ticker["lastPrice"]),
        quote_volume_24h=float(ticker["quoteVolume"]),
        funding_rate=float(funding["lastFundingRate"]) if funding.get("lastFundingRate") is not None else None,
        open_interest=current_oi,
        oi_change_1h=oi_change(12),
        oi_change_4h=oi_change(48),
        oi_change_24h=oi_change(288),
    )


def collect_market(symbols: list[str] | None = None, pause_seconds: float = 0.08) -> list[dict[str, Any]]:
    # Bootstrap requests intentionally fail fast: if the runner itself is blocked,
    # returning hundreds of per-symbol errors would hide the infrastructure issue.
    symbols = symbols or active_usdt_perpetuals()
    tickers = {x["symbol"]: x for x in _get("/fapi/v1/ticker/24hr") if x.get("symbol") in symbols}
    premiums = {x["symbol"]: x for x in _get("/fapi/v1/premiumIndex") if x.get("symbol") in symbols}
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            rows.append(asdict(collect_symbol(symbol, tickers.get(symbol), premiums.get(symbol))))
        except Exception as exc:
            rows.append({"symbol": symbol, "observed_at": datetime.now(timezone.utc).isoformat(), "error": str(exc)})
        time.sleep(pause_seconds)
    return rows


if __name__ == "__main__":
    print(json.dumps(collect_market(), ensure_ascii=False))
