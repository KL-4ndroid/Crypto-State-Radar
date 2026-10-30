import json
from datetime import datetime, timezone
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from src.research_journal import ResearchJournal

class JournalReader:
    def __init__(self,root):
        self.root = root
    def read(self,kind):
        store = ResearchJournal(Path(self.root)/"research.sqlite3")
        try:
            return store.read(kind)
        finally:
            store.close()

def JsonlResearchStore(root):
    return JournalReader(root)
from src.run_cycle import _enrichment_candidates, _features, run


@pytest.fixture(autouse=True)
def fake_enrichment(monkeypatch):
    monkeypatch.setattr("src.run_cycle.derive_live_features", lambda symbol: {"price_change_1h": 1.0, "price_change_4h": 1.0})


def snapshot(symbol="EVAAUSDT", price=1.1, oi1=-0.03, oi4=-0.08, oi24=0.4):
    return {"symbol": symbol, "observed_at": "2026-10-29T12:00:00+00:00", "price": price, "quote_volume_24h": 1_000_000, "funding_rate": 0.0001, "open_interest": 10_000_000, "oi_change_1h": oi1, "oi_change_4h": oi4, "oi_change_24h": oi24}


def test_oi_decimal_ratios_become_percentage_points_at_feature_boundary():
    features = _features(snapshot(oi1=0.05, oi4=-0.08, oi24=0.4))
    assert features["oi_change_1h"] == 5.0
    assert features["oi_change_4h"] == -8.0
    assert features["oi_change_24h"] == 40.0


def test_invalid_oi_is_missing_not_zero():
    features = _features(snapshot(oi1="invalid", oi4=float("nan"), oi24=0))
    assert features["oi_change_1h"] is None
    assert features["oi_change_4h"] is None
    assert features["oi_change_24h"] == 0


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
        assert payload["states"]["BADUSDT"]["evaluation_status"] == "pending"


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
        assert signals[0]["entry_price"] == 1.1
        assert len(transitions) == 1
        assert transitions[0]["from_state"] == "WATCH"
        assert transitions[0]["to_state"] == "EARLY_WARNING"

        payload = json.loads(state_path.read_text(encoding="utf-8"))
        saved = payload["states"]["EVAAUSDT"]
        assert saved["state"] == "EARLY_WARNING"
        assert saved["peak_oi"] == 10_000_000.0
        assert saved["trough_oi"] == 10_000_000.0


def test_real_classifier_receives_enrichment_and_writes_validatable_signal(monkeypatch, tmp_path):
    monkeypatch.setenv("CSR_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.delenv("CSR_STATE_STORE_URL", raising=False)
    monkeypatch.setattr("src.run_cycle.collect_market", lambda symbols=None: [snapshot(oi1=0.05, oi4=0.05, oi24=0)])
    monkeypatch.setattr("src.run_cycle.derive_live_features", lambda symbol: {
        "price_change_1h": 5.0, "price_change_4h": 3.0, "volume_z": 3.0,
        "volatility_compression": 1.0,
    })
    result = run(root=str(tmp_path))
    signal = list(JsonlResearchStore(tmp_path).read("signals"))[0]
    assert result["enrichment_succeeded"] == 1
    assert signal["state"] == "LONG_READY"
    assert signal["entry_price"] == 1.1
    assert signal["model_version"] == "V2.4.1-alpha"
    import validation_cycle
    from datetime import timedelta
    observed = datetime.fromisoformat(signal["observed_at"])
    first = observed.replace(second=0, microsecond=0)
    first += timedelta(minutes=(5 - first.minute % 5) % 5)
    if first < observed:
        first += timedelta(minutes=5)
    candles = [{"time": first + timedelta(minutes=5 * i), "high": 1.2, "low": 1.0, "close": 1.15} for i in range(11)]
    monkeypatch.setattr(validation_cycle, "_candles", lambda *args: candles)
    store = ResearchJournal(tmp_path / "research.sqlite3")
    try:
        validation = validation_cycle.run_validation_cycle(store, now=observed + timedelta(hours=1, minutes=11))
    finally:
        store.close()
    assert validation["new_validations"] == 1
    assert validation["validated"][0]["entry_price"] == 1.1


def test_failed_enrichment_preserves_previous_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CSR_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.delenv("CSR_STATE_STORE_URL", raising=False)
    monkeypatch.setattr("src.run_cycle.collect_market", lambda symbols=None: [snapshot()])
    monkeypatch.setattr("src.run_cycle.derive_live_features", lambda symbol: {})
    result = run(root=str(tmp_path))
    assert result["enrichment_errors"] == 1
    assert result["enrichment_failures"] == [{"symbol": "EVAAUSDT", "reason": "insufficient_price_history"}]
    assert result["signals"] == 0
    assert json.loads((tmp_path / "state.json").read_text())["states"]["EVAAUSDT"]["updated_at"] is None


def test_missing_oi_history_does_not_advance_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CSR_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.delenv("CSR_STATE_STORE_URL", raising=False)
    monkeypatch.setattr("src.run_cycle.collect_market", lambda symbols=None: [snapshot(oi24=None)])
    result = run(root=str(tmp_path))
    assert result["enrichment_succeeded"] == 0
    assert result["enrichment_failures"][0]["reason"] == "insufficient_market_data"
    assert json.loads((tmp_path / "state.json").read_text())["states"]["EVAAUSDT"]["updated_at"] is None


def test_candidate_selection_is_bounded_and_prioritizes_active():
    from src.state_machine import SymbolState
    rows = [snapshot(symbol=f"COIN{i}USDT", oi1=i / 1000) for i in range(50)]
    previous = {"COIN0USDT": SymbolState(symbol="COIN0USDT", state="LONG_READY")}
    selected = _enrichment_candidates(rows, previous, datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert len(selected) == 25
    assert selected[0]["symbol"] == "COIN0USDT"


def test_discovery_has_capacity_when_many_states_are_active():
    from src.state_machine import SymbolState
    rows = [snapshot(symbol=f"COIN{i:02}USDT", oi1=i / 1000) for i in range(50)]
    previous = {row["symbol"]: SymbolState(symbol=row["symbol"], state="LONG_READY", updated_at="2026-10-29T00:00:00+00:00") for row in rows[:30]}
    selected = _enrichment_candidates(rows, previous, datetime(2026, 10, 30, tzinfo=timezone.utc))
    assert len(selected) == 25
    assert all(s["symbol"] in previous for s in selected)  # overflow stays pinned; discovery resumes after draining


def test_committed_outbox_can_publish_after_failure_without_rescanning(monkeypatch,tmp_path):
    from src.state_store import FileStateStore
    from src.research_worker import work
    monkeypatch.setenv("CSR_STATE_FILE",str(tmp_path/"state.json"))
    monkeypatch.delenv("CSR_STATE_STORE_URL",raising=False)
    monkeypatch.setattr("src.run_cycle.collect_market",lambda symbols=None:[snapshot()])
    with patch.object(FileStateStore,"publish_snapshot",side_effect=RuntimeError("unavailable")):
        summary=run(root=str(tmp_path))
    assert summary["snapshot_published"] is False
    assert summary["publication_error"]=="RuntimeError"
    # A worker replays the DB snapshot; it does not repeat the market scan.
    result=work("publish",root=str(tmp_path))
    assert result["published"] and result["revision"]==1
    assert json.loads((tmp_path/"state.json").read_text())["revision"]==1


def test_continuation_waits_for_retry_without_fetching_market(monkeypatch,tmp_path):
    from datetime import timedelta
    from src.research_journal import ResearchJournal
    monkeypatch.setenv("CSR_STATE_FILE",str(tmp_path/"state.json"))
    monkeypatch.delenv("CSR_STATE_STORE_URL",raising=False)
    now=datetime.now(timezone.utc)
    store=ResearchJournal(tmp_path/"research.sqlite3")
    try:store.commit_cycle(0,{"states":{},"pending":{"A":{"reason":"retry","due_since":now.isoformat(),"next_attempt_at":(now+timedelta(hours=1)).isoformat()}}},[])
    finally:store.close()
    with patch("src.run_cycle.collect_market") as collect:
        result=run(root=str(tmp_path),drain=True)
        collect.assert_not_called()
    assert result["status"]=="waiting" and result["pending"]==1


def test_legacy_ready_without_anchor_is_counted_as_pending_not_success(monkeypatch,tmp_path):
    monkeypatch.setenv("CSR_STATE_FILE",str(tmp_path/"state.json"))
    monkeypatch.delenv("CSR_STATE_STORE_URL",raising=False)
    (tmp_path/"state.json").write_text(json.dumps({"states":{"EVAAUSDT":{"symbol":"EVAAUSDT","state":"LONG_READY","ready_price":1.1}}}))
    monkeypatch.setattr("src.run_cycle.collect_market",lambda symbols=None:[snapshot()])
    result=run(root=str(tmp_path))
    assert result["enrichment_succeeded"]==0 and result["pending"]==1
    assert result["enrichment_failures"][0]["reason"]=="legacy_ready_anchor_missing"
