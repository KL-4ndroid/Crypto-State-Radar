from datetime import datetime, timedelta, timezone

from validation_cycle import signal_id
from validate_signals import validate


def test_signal_id_is_deterministic():
    signal = {
        "symbol": "EVAAUSDT",
        "observed_at": "2026-10-29T00:00:00+00:00",
        "model_version": "V2.3.0-alpha",
        "state": "SHORT_READY",
    }
    assert signal_id(signal) == signal_id(dict(signal))
    assert len(signal_id(signal)) == 24


def test_existing_signal_id_is_preserved():
    assert signal_id({"signal_id": "fixed-id"}) == "fixed-id"


def test_validation_excludes_candle_at_horizon():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [{"time": start + timedelta(minutes=5*i), "high": 101, "low": 99, "close": 100} for i in range(12)]
    candles.append({"time": start + timedelta(hours=1), "high": 1000, "low": 1, "close": 500})
    result = validate("signal", start, 100, candles, "1h", "LONG")
    assert result is not None
    assert result.end_price == 100
    assert result.mfe_pct == 1
    assert result.mae_pct == -1


def test_validation_rejects_missing_candles_and_invalid_entry():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [{"time": start + timedelta(minutes=5*i), "high": 101, "low": 99, "close": 100} for i in range(12)]
    assert validate("s", start, 0, candles, "1h", "LONG") is None
    del candles[5]
    assert validate("s", start, 100, candles, "1h", "LONG") is None
