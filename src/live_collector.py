"""Binance USDT-M live collector for Crypto-State-Radar.

Collects objective market observations only. No trading decisions are made here.
OI changes are stored as decimal ratios (0.05 == +5%); conversion to percentage
points happens at the feature boundary in run_cycle.py.
"""
from __future__ import annotations

import json
import time
try:
    from .request_budget import CURRENT
except ImportError:
    from request_budget import CURRENT
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

DEFAULT_BASE_URL = "https://fapi.binance.com"
BASE_URL = os.getenv("CSR_BINANCE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
OI_HISTORY_LIMIT = 289  # current 5m bar + 288 bars back = 24 hours
DEFAULT_OI_WORKERS = 8
MAX_OI_WORKERS = 16


class MarketDataUnavailable(RuntimeError):
    """Raised when the configured market-data origin cannot serve the runner."""


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Crypto-State-Radar/0.2"})
    budget = CURRENT.get()
    timeout = budget.begin() if budget else 15
    started, failed = time.monotonic(), True
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
            failed = False
            return result
    except urllib.error.HTTPError as exc:
        if exc.code == 451:
            raise MarketDataUnavailable(
                "Binance Futures returned HTTP 451 for this runner location. "
                "Run the collector from a Binance-supported region or set "
                "CSR_BINANCE_BASE_URL to an approved market-data gateway you control."
            ) from exc
        raise
    finally:
        if budget:
            budget.finish(time.monotonic()-started,failed)


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
    price_change_24h: float | None = None


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
    # We only derive 1h / 4h / 24h OI deltas, so requesting 500 points was
    # unnecessary payload. 289 five-minute points are exactly enough to compare
    # the current observation with 288 bars (24 hours) ago.
    return _get(
        "/futures/data/openInterestHist",
        {"symbol": symbol, "period": "5m", "limit": OI_HISTORY_LIMIT},
    )


def _oi_worker_count() -> int:
    """Return a conservative bounded worker count for per-symbol OI history."""
    raw = os.getenv("CSR_OI_WORKERS", str(DEFAULT_OI_WORKERS))
    try:
        workers = int(raw)
    except (TypeError, ValueError):
        workers = DEFAULT_OI_WORKERS
    return max(1, min(MAX_OI_WORKERS, workers))


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
        price_change_24h=float(ticker["priceChangePercent"]) if ticker.get("priceChangePercent") is not None else None,
    )


def collect_market(symbols: list[str] | None = None, pause_seconds: float = 0.0) -> list[dict[str, Any]]:
    """Collect one full-market snapshot with bounded parallel OI requests.

    Batch ticker and premium-index endpoints are still fetched only once. The
    expensive per-symbol OI history requests are executed with a small bounded
    worker pool instead of serially. ``pause_seconds`` remains in the signature
    for backwards compatibility but is intentionally unused; concurrency is
    bounded by ``CSR_OI_WORKERS`` (default 8, maximum 16).
    """
    del pause_seconds

    # Bootstrap requests intentionally fail fast: if the runner itself is blocked,
    # returning hundreds of per-symbol errors would hide the infrastructure issue.
    symbols = symbols or active_usdt_perpetuals()
    tickers = {x["symbol"]: x for x in _get("/fapi/v1/ticker/24hr") if x.get("symbol") in symbols}
    premiums = {x["symbol"]: x for x in _get("/fapi/v1/premiumIndex") if x.get("symbol") in symbols}

    budget = CURRENT.get()

    def collect_one(symbol: str) -> dict[str, Any]:
        CURRENT.set(budget)
        try:
            return asdict(collect_symbol(symbol, tickers.get(symbol), premiums.get(symbol)))
        except Exception as exc:
            return {
                "symbol": symbol,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            }

    if not symbols:
        return []

    workers = min(_oi_worker_count(), len(symbols))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="radar-oi") as executor:
        # executor.map preserves input order, keeping journal/state output stable.
        return list(executor.map(collect_one, symbols))


if __name__ == "__main__":
    print(json.dumps(collect_market(), ensure_ascii=False))
