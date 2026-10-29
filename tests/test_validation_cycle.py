from datetime import datetime, timezone

from validation_cycle import signal_id


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
