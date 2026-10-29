from src.store import SqliteResearchStore


def test_signal_is_immutable_and_validation_is_idempotent(tmp_path):
    store = SqliteResearchStore(tmp_path / "research.sqlite3")
    signal = {"signal_id":"s1","symbol":"EVAAUSDT","observed_at":"2026-10-29T00:00:00+00:00","model_version":"V2.3.0-alpha","state":"SHORT_READY","direction":"SHORT","entry_price":1.0}
    store.append_signal(signal)
    changed = dict(signal); changed["entry_price"] = 999.0
    store.append_signal(changed)
    assert store.read("signals")[0]["entry_price"] == 1.0

    validation = {"signal_id":"s1","horizon":"1h","validated_at":"2026-10-29T01:00:00+00:00","outcome":"SUCCESS"}
    store.append_validation(validation)
    store.append_validation(dict(validation, outcome="FAILURE"))
    rows = store.read("validations")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "SUCCESS"
    store.close()
