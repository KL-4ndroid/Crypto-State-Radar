import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.persistence import JsonlResearchStore
from src.run_cycle import _features, run


def snapshot(symbol="EVAAUSDT", price=1.1, oi1=-0.03, oi4=-0.08, oi24=0.4):
    return {"symbol": symbol, "observed_at": "2026-10-29T12:00:00+00:00", "price": price, "quote_volume_24h": 1_000_000, "funding_rate": 0.0001, "open_interest": 10_000_000, "oi_change_1h": oi1, "oi_change_4h": oi4, "oi_change_24h": oi24}


def test_oi_decimal_ratios_become_percentage_points_at_feature_boundary():
    features = _features(snapshot(oi1=0.05, oi4=-0.08, oi24=0.4))
    assert features["oi_change_1h"] == 5.0
    assert features["oi_change_4h"] == -8.0
    assert features["oi_change_24h"] == 40.0


def test_cycle_always_records_objective_observation(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        monkeypatch.setenv("CSR_STATE_FILE", str(Path(root, "state.json")))
        monkeypatch.delenv("CSR_STATE_STORE_URL", raising=False)
        with patch("src.run_cycle.collect_market", return_value=[snapshot()]):
            result = run(["EVAAUSDT"], root=root)
        store = JsonlResearchStore(root)
        assert result["scanned"] == 1
        assert len(list(store.read("observations"))) == 1
        assert result["priority"][0]["symbol"] == "EVAAUSDT"


def test_collector_error_is_recorded_without_aborting_cycle(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root, "state.json")
        monkeypatch.setenv("CSR_STATE_FILE", str(state_path))
        monkeypatch.delenv("CSR_STATE_STORE_URL", raising=False)
        rows = [{"symbol": "BADUSDT", "observed_at": "2026-10-29T12:00:00+00:00", "error": "temporary"}, snapshot("ONUSDT")]
        with patch("src.run_cycle.collect_market", return_value=rows):
            result = run(root=root)
        assert result["errors"] == 1
        assert result["scanned"] == 1
        assert state_path.exists()
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert "ONUSDT" in payload["states"]
        assert "BADUSDT" not in payload["states"]


def test_repeated_signal_state_is_persisted_without_duplicate_signal(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root, "state.json")
        monkeypatch.setenv("CSR_STATE_FILE", str(state_path))
        monkeypatch.delenv("CSR_STATE_STORE_URL", raising=False)
        decision = {
            "symbol": "EVAAUSDT",
            "observed_at": "2026-10-29T12:00:00+00:00",
            "model_version": "test",
            "pump_score": 10.0,
            "dump_score": 45.0,
            "state": "EARLY_WARNING",
            "direction": "SHORT_WATCH",
            "ttl_minutes": None,
            "expires_at": None,
            "reasons": ["OI_ACCELERATION"],
            "invalidation": "test",
            "features": {"price": 1.1, "open_interest": 10_000_000, "price_change_1h": 0.0, "oi_change_1h": 5.0},
        }
        classified = SimpleNamespace(to_dict=lambda: dict(decision))
        with patch("src.run_cycle.collect_market", return_value=[snapshot(oi1=0.05)]), patch("src.run_cycle.classify", return_value=classified):
            first = run(["EVAAUSDT"], root=root)
            second = run(["EVAAUSDT"], root=root)

        journal = JsonlResearchStore(root)
        signals = list(journal.read("signals"))
        transitions = list(journal.read("transitions"))
        assert first["signals"] == 1
        assert second["signals"] == 0
        assert len(signals) == 1
        assert len(transitions) == 1
        assert transitions[0]["from_state"] == "WATCH"
        assert transitions[0]["to_state"] == "EARLY_WARNING"

        payload = json.loads(state_path.read_text(encoding="utf-8"))
        saved = payload["states"]["EVAAUSDT"]
        assert saved["state"] == "EARLY_WARNING"
        assert saved["peak_oi"] == 10_000_000.0
        assert saved["trough_oi"] == 10_000_000.0
