from datetime import datetime, timedelta, timezone

from src.state_machine import SymbolState, transition


def decision(state, direction, price, p1, oi1, expires_at=None):
    return {
        "state": state,
        "direction": direction,
        "expires_at": expires_at,
        "model_version": "test",
        "features": {"price": price, "open_interest": 100.0, "price_change_1h": p1, "oi_change_1h": oi1},
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
