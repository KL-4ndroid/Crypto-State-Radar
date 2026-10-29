from datetime import datetime, timedelta, timezone

from src.state_machine import JsonStateStore, SymbolState, decode_snapshot, encode_snapshot, transition


def decision(state, direction, price, p1, oi1, expires_at=None, open_interest=100.0):
    return {
        "state": state,
        "direction": direction,
        "expires_at": expires_at,
        "model_version": "test",
        "features": {"price": price, "open_interest": open_interest, "price_change_1h": p1, "oi_change_1h": oi1},
    }


def test_short_ready_can_trigger_on_further_breakdown():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prev = SymbolState("EVAAUSDT", "SHORT_READY", "SHORT", now.isoformat(), now.isoformat(),
                       (now + timedelta(hours=4)).isoformat(), 1.20, 100.0, "test", 0)
    state, event = transition(prev, decision("SHORT_READY", "SHORT", 1.15, -2, -3, prev.expires_at), now + timedelta(minutes=5))
    assert state.state == "TRIGGERED"
    assert event["reason"] == "BREAKDOWN_AFTER_SHORT_READY"


def test_short_ready_invalidates_on_price_and_oi_reexpansion():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prev = SymbolState("ONUSDT", "SHORT_READY", "SHORT", now.isoformat(), now.isoformat(),
                       (now + timedelta(hours=4)).isoformat(), 0.2, 100.0, "test", 0)
    state, event = transition(prev, decision("DUMP_PRESSURE", "SHORT_WATCH", 0.21, 2, 3), now + timedelta(minutes=5))
    assert state.state == "INVALIDATED"
    assert event["reason"] == "PRICE_AND_OI_REEXPAND"


def test_ready_expires_when_ttl_passes_and_confirmation_is_gone():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prev = SymbolState("PREDICTUSDT", "SHORT_READY", "SHORT", now.isoformat(), now.isoformat(),
                       (now + timedelta(hours=1)).isoformat(), 1.4, 100.0, "test", 0)
    state, event = transition(prev, decision("WATCH", "NEUTRAL", 1.39, 0, 0), now + timedelta(hours=2))
    assert state.state == "EXPIRED"
    assert event["reason"] == "TTL_EXPIRED"


def test_terminal_state_starts_new_cycle_when_setup_returns():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prev = SymbolState("EVAAUSDT", "INVALIDATED", "SHORT", now.isoformat(), now.isoformat(), None, 1.2, 100, "test", 2)
    state, event = transition(prev, decision("DUMP_PRESSURE", "SHORT_WATCH", 1.18, -1, 2), now + timedelta(hours=3))
    assert state.state == "DUMP_PRESSURE"
    assert state.cycle == 3
    assert event["reason"] == "NEW_CYCLE"


def test_snapshot_round_trip_preserves_sequence_memory():
    original = SymbolState(
        symbol="EVAAUSDT", state="DUMP_PRESSURE", direction="SHORT_WATCH",
        cycle=4, peak_price=1.42, peak_oi=125.0, trough_price=1.08,
        trough_oi=88.0, last_transition="WATCH->DUMP_PRESSURE",
    )
    restored = decode_snapshot(encode_snapshot({"EVAAUSDT": original}))["EVAAUSDT"]
    assert restored == original


def test_legacy_snapshot_remains_readable():
    legacy = {"ONUSDT": {"symbol": "ONUSDT", "state": "WATCH", "cycle": 2}}
    restored = decode_snapshot(legacy)["ONUSDT"]
    assert restored.state == "WATCH"
    assert restored.cycle == 2
    assert restored.peak_oi is None


def test_json_store_persists_versioned_snapshot(tmp_path):
    path = tmp_path / "state.json"
    store = JsonStateStore(path)
    store.put(SymbolState("PREDICTUSDT", state="SHORT_READY", peak_oi=150.0))
    reopened = JsonStateStore(path)
    assert reopened.get("PREDICTUSDT").state == "SHORT_READY"
    assert reopened.get("PREDICTUSDT").peak_oi == 150.0


def test_transition_tracks_price_and_oi_extremes():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prev = SymbolState("BLESSUSDT", peak_price=1.0, peak_oi=100.0, trough_price=1.0, trough_oi=100.0)
    state, _ = transition(prev, decision("WATCH", "NEUTRAL", 1.2, 2, 5, open_interest=120.0), now)
    assert state.peak_price == 1.2
    assert state.peak_oi == 120.0
    assert state.trough_price == 1.0
    assert state.trough_oi == 100.0
