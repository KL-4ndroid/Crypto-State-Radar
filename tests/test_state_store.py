import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.state_machine import SymbolState
from src.state_store import FileStateStore, UpstashRestStateStore, state_store_from_env


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
    monkeypatch.delenv("KV_REST_API_URL", raising=False)
    monkeypatch.delenv("KV_REST_API_TOKEN", raising=False)
    monkeypatch.delenv("CSR_STATE_STORE_URL", raising=False)
    monkeypatch.setenv("CSR_STATE_FILE", str(path))
    store = state_store_from_env()
    assert isinstance(store, FileStateStore)


def _response(payload: bytes):
    response = MagicMock()
    response.read.return_value = payload
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def test_upstash_commands_use_post_body_not_url_path():
    with patch("src.state_store.urllib.request.urlopen", return_value=_response(b'{"result":null}')) as mocked:
        store = UpstashRestStateStore("https://example.upstash.io", "secret")

    assert store.all() == {}
    request = mocked.call_args.args[0]
    assert request.full_url == "https://example.upstash.io"
    assert request.method == "POST"
    assert json.loads(request.data.decode("utf-8")) == ["GET", "crypto-state-radar:state:v1"]


def test_env_factory_prefers_upstash_inside_vercel(monkeypatch):
    monkeypatch.setenv("KV_REST_API_URL", "https://example.upstash.io")
    monkeypatch.setenv("KV_REST_API_TOKEN", "secret")
    monkeypatch.setenv("CSR_STATE_STORE_URL", "https://bridge.example/api/state")
    with patch("src.state_store.urllib.request.urlopen", return_value=_response(b'{"result":null}')):
        store = state_store_from_env()
    assert isinstance(store, UpstashRestStateStore)
