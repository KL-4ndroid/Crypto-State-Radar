"""Pinned acceptance cases. Baseline WATCH follows main 5816d3a's real feed."""
import json
from pathlib import Path
from datetime import datetime, timezone
import pytest
from src.model_contract import snapshot_features, history_features, MODEL_VERSION
from src.radar_engine import classify, score
from src.state_machine import SymbolState, transition

CASES = json.loads((Path(__file__).parent / "fixtures/market_cases.json").read_text())
NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_fixed_market_cases(case):
    if case.get("error"):
        return  # Exercised as a failed observation by the cycle acceptance test.
    features = snapshot_features({"price": case["price"], "open_interest": 100,
        **{f"oi_change_{h}h": case[f"oi{h}"] for h in (1, 4, 24)}})
    # Exactly the feed on pinned main: optional deep features are absent.
    assert classify("TEST", features, NOW).state == "WATCH"
    features.update({"price_change_1h": case.get("p1"), "price_change_4h": case.get("p4"),
        "volume_z": case.get("volume_z"), "volatility_compression": case.get("compression"),
        "funding_z": case.get("funding_z"), "funding_acceleration": case.get("funding_acceleration")})
    decision = classify("TEST", features, NOW)
    assert decision.model_version == MODEL_VERSION
    if case.get("previous"):
        previous = SymbolState("TEST", state=case["previous"], ready_price=100, direction="LONG",entered_at=NOW.isoformat(),expires_at="2026-10-30T04:00:00+00:00")
        state, _ = transition(previous, decision.to_dict(), NOW)
        assert state.state == case["expected"]
    else:
        assert decision.state == case["expected"]


def test_normal_volatility_and_missing_price_do_not_add_false_evidence():
    assert score({"volatility_compression": 1})[:2] == (0, 0)
    assert score({"volatility_compression": 0.6})[:2] == (10, 10)
    assert score({"price_change_1h": None, "funding_z": -4, "funding_acceleration": -4})[:2] == (0, 0)


def test_live_and_offline_share_identical_raw_history(monkeypatch):
    from src import live_features
    cutoff = int(NOW.timestamp()*1000)
    rows = []
    for i in range(1500):
        t = cutoff - (1500-i)*300000
        price = 100 + i/100 + (i % 2)/10
        rows.append([t, price, price, price, price, 1, t+299999, 1+i%31])
    funding = [{"fundingTime":cutoff-i*6*3600000, "fundingRate":i/100000} for i in range(25)]
    monkeypatch.setattr(live_features.time, "time", lambda: NOW.timestamp())
    monkeypatch.setattr(live_features, "_get", lambda path, params: rows if path.endswith("klines") else funding)
    expected = history_features(rows, funding, NOW)
    assert live_features.derive_live_features("TEST") == expected
    assert classify("TEST", expected, NOW).to_dict() == classify("TEST", history_features(rows, funding, NOW), NOW).to_dict()
    assert expected["funding_z"] is not None
    assert expected["volatility_compression"] is not None
    assert history_features(rows, funding[:3], NOW)["funding_z"] is None
    normal_8h = [{"fundingTime":cutoff-i*8*3600000,"fundingRate":i/100000} for i in range(21)]
    assert history_features(rows,normal_8h,NOW)["funding_z"] is not None
    dense_new_listing = [{"fundingTime":cutoff-i*3600000,"fundingRate":i/100000} for i in range(21)]
    assert history_features(rows,dense_new_listing,NOW)["funding_z"] is None
