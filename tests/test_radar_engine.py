from src.radar_engine import classify


def test_short_ready_requires_price_confirmation_and_oi_retreat():
    f = {
        "price_change_1h": -5.0, "price_change_4h": -9.0,
        "oi_change_1h": -4.0, "oi_change_4h": -8.0, "oi_change_24h": 60.0,
        "funding_z": 3.0, "funding_acceleration": 2.0,
        "volume_z": 5.0, "volatility_compression": 0.8,
    }
    d = classify("TESTUSDT", f)
    assert d.state == "SHORT_READY"
    assert d.ttl_minutes == 240
    assert "OI_RETREAT" in d.reasons


def test_bearish_build_without_retreat_is_not_short_ready():
    f = {
        "price_change_1h": -3.0, "price_change_4h": -6.0,
        "oi_change_1h": 8.0, "oi_change_4h": 20.0, "oi_change_24h": 35.0,
        "funding_z": 2.5, "volume_z": 4.0,
    }
    d = classify("TESTUSDT", f)
    assert d.state in {"DUMP_PRESSURE", "EARLY_WARNING"}
    assert d.state != "SHORT_READY"


def test_long_ready_requires_price_and_oi_expansion():
    f = {
        "price_change_1h": 5.0, "price_change_4h": 12.0,
        "oi_change_1h": 7.0, "oi_change_4h": 15.0, "oi_change_24h": 20.0,
        "funding_z": -2.0, "volume_z": 5.0,
    }
    d = classify("TESTUSDT", f)
    assert d.state == "LONG_READY"
