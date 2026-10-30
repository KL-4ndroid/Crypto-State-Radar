import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import live_features
from live_features import _ret, _z


def test_return_is_percentage():
    assert _ret(110.0, 100.0) == 10.0


def test_z_requires_history():
    assert _z(1.0, [1.0, 1.1]) is None


def test_z_detects_outlier():
    value = _z(10.0, [1, 2, 3, 4, 5, 6])
    assert value is not None and value > 2


def test_closed_candles_use_non_overlapping_hourly_volume(monkeypatch):
    rows = []
    for i in range(302):
        # Vary historical hours; an unfinished final candle has enormous volume.
        volume = 10000 if i == 301 else (100 if i >= 289 else 1 + i // 12)
        rows.append([i * 300000, 0, 0, 0, str(100 + i), 0, i * 300000 + 299999, str(volume)])
    monkeypatch.setattr(live_features.time, "time", lambda: (301 * 300000 + 1000) / 1000)
    monkeypatch.setattr(live_features, "_get", lambda path, params: rows if path.endswith("klines") else [])
    features = live_features.derive_live_features("TESTUSDT")
    assert features["volume_1h"] == 1200
    assert features["volume_z"] > 5
    assert features["price_change_1h"] == _ret(400, 388)


def test_gap_in_candles_does_not_create_bar_based_returns(monkeypatch):
    rows = [[i * 300000, 0, 0, 0, "100", 0, i * 300000 + 299999, "1"] for i in range(301)]
    del rows[100]
    monkeypatch.setattr(live_features.time, "time", lambda: 1_000_000_000)
    monkeypatch.setattr(live_features, "_get", lambda path, params: rows if path.endswith("klines") else [])
    assert live_features.derive_live_features("TESTUSDT")["price_change_1h"] is None
