from __future__ import annotations

import time

import src.live_collector as collector


def test_oi_history_uses_only_required_24h_points(monkeypatch):
    seen = {}

    def fake_get(path, params=None):
        seen["path"] = path
        seen["params"] = params
        return []

    monkeypatch.setattr(collector, "_get", fake_get)

    assert collector._oi_history("BTCUSDT") == []
    assert seen["path"] == "/futures/data/openInterestHist"
    assert seen["params"] == {"symbol": "BTCUSDT", "period": "5m", "limit": 289}


def test_worker_count_is_bounded(monkeypatch):
    monkeypatch.setenv("CSR_OI_WORKERS", "0")
    assert collector._oi_worker_count() == 1

    monkeypatch.setenv("CSR_OI_WORKERS", "999")
    assert collector._oi_worker_count() == collector.MAX_OI_WORKERS

    monkeypatch.setenv("CSR_OI_WORKERS", "invalid")
    assert collector._oi_worker_count() == collector.DEFAULT_OI_WORKERS


def test_collect_market_preserves_order_and_parallelizes_symbol_work(monkeypatch):
    symbols = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]

    def fake_get(path, params=None):
        if path == "/fapi/v1/ticker/24hr":
            return [
                {"symbol": symbol, "lastPrice": "1", "quoteVolume": "10"}
                for symbol in symbols
            ]
        if path == "/fapi/v1/premiumIndex":
            return [{"symbol": symbol, "lastFundingRate": "0"} for symbol in symbols]
        raise AssertionError(f"unexpected batch path: {path}")

    def fake_collect_symbol(symbol, ticker=None, funding=None):
        # Serial execution would take ~0.20s; four workers should finish well below it.
        time.sleep(0.05)
        return collector.MarketSnapshot(
            symbol=symbol,
            observed_at="2026-10-30T00:00:00+00:00",
            price=float(ticker["lastPrice"]),
            quote_volume_24h=float(ticker["quoteVolume"]),
            funding_rate=float(funding["lastFundingRate"]),
            open_interest=100.0,
            oi_change_1h=0.01,
            oi_change_4h=0.02,
            oi_change_24h=0.03,
        )

    monkeypatch.setattr(collector, "_get", fake_get)
    monkeypatch.setattr(collector, "collect_symbol", fake_collect_symbol)
    monkeypatch.setenv("CSR_OI_WORKERS", "4")

    started = time.monotonic()
    rows = collector.collect_market(symbols=symbols)
    elapsed = time.monotonic() - started

    assert [row["symbol"] for row in rows] == symbols
    assert elapsed < 0.18
