import json

from src.export_dataset import export_bundle
from src.persistence import JsonlResearchStore


def test_signal_journal_is_append_only(tmp_path):
    store = JsonlResearchStore(tmp_path / "journal")
    store.append_signal({"id": "s1", "symbol": "EVAAUSDT", "state": "EARLY_WARNING"})
    store.append_signal({"id": "s2", "symbol": "EVAAUSDT", "state": "SHORT_READY"})
    rows = list(store.read("signals"))
    assert [row["id"] for row in rows] == ["s1", "s2"]


def test_export_bundle_keeps_signal_and_validation_separate(tmp_path):
    journal = tmp_path / "journal"
    store = JsonlResearchStore(journal)
    store.append_signal({"id": "s1", "symbol": "PREDICTUSDT", "price": 1.36})
    store.append_validation({"signal_id": "s1", "horizon": "24h", "return": -0.12})
    out = export_bundle(str(journal), str(tmp_path / "exports"))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["datasets"]["signals"]["rows"] == 1
    assert manifest["datasets"]["validations"]["rows"] == 1
    assert (out / "signals.csv").exists()
    assert (out / "validations.csv").exists()
