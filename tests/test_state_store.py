import json
from pathlib import Path

from src.state_machine import SymbolState
from src.state_store import FileStateStore, state_store_from_env


def test_file_store_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"
    store = FileStateStore(path)
    store.put(SymbolState(symbol="EVAAUSDT", state="SHORT_READY", peak_oi=125.0, cycle=2))

    loaded = FileStateStore(path)
    state = loaded.get("EVAAUSDT")
    assert state.state == "SHORT_READY"
    assert state.peak_oi == 125.0
    assert state.cycle == 2

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert "EVAAUSDT" in payload["states"]


def test_file_store_returns_fresh_watch_for_unknown_symbol(tmp_path: Path):
    store = FileStateStore(tmp_path / "state.json")
    state = store.get("NEWUSDT")
    assert state.symbol == "NEWUSDT"
    assert state.state == "WATCH"


def test_env_factory_defaults_to_file_store(monkeypatch, tmp_path: Path):
    path = tmp_path / "configured.json"
    monkeypatch.delenv("CSR_STATE_STORE_URL", raising=False)
    monkeypatch.setenv("CSR_STATE_FILE", str(path))
    store = state_store_from_env()
    assert isinstance(store, FileStateStore)
