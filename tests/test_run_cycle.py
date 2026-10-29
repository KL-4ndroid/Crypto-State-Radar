import tempfile
from pathlib import Path
from unittest.mock import patch

from src.persistence import JsonlResearchStore
from src.run_cycle import run


def snapshot(symbol="EVAAUSDT", price=1.1, oi1=-0.03, oi4=-0.08, oi24=0.4):
    return {"symbol": symbol, "observed_at": "2026-10-29T12:00:00+00:00", "price": price, "quote_volume_24h": 1_000_000, "funding_rate": 0.0001, "open_interest": 10_000_000, "oi_change_1h": oi1, "oi_change_4h": oi4, "oi_change_24h": oi24}


def test_cycle_always_records_objective_observation():
    with tempfile.TemporaryDirectory() as root:
        with patch("src.run_cycle.collect_market", return_value=[snapshot()]):
            result = run(["EVAAUSDT"], root=root)
        store = JsonlResearchStore(root)
        assert result["scanned"] == 1
        assert len(list(store.read("observations"))) == 1
        assert result["priority"][0]["symbol"] == "EVAAUSDT"


def test_collector_error_is_recorded_without_aborting_cycle():
    with tempfile.TemporaryDirectory() as root:
        rows = [{"symbol": "BADUSDT", "observed_at": "2026-10-29T12:00:00+00:00", "error": "temporary"}, snapshot("ONUSDT")]
        with patch("src.run_cycle.collect_market", return_value=rows):
            result = run(root=root)
        assert result["errors"] == 1
        assert result["scanned"] == 1
        assert Path(root, "latest_states.json").exists()
