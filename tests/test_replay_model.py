from datetime import datetime,timedelta,timezone
import pytest
from src.replay_model import replay
from src.event_evaluation import detect_events


def frame(as_of,selected=True):
    return {"as_of":as_of,"selected":selected,"snapshot":{"symbol":"A","observed_at":as_of,"price":100,"open_interest":100,
        "oi_change_1h":0.05,"oi_change_4h":0.1,"oi_change_24h":0.24},"klines":[],"funding":[]}


@pytest.fixture
def complete_features(monkeypatch):
    monkeypatch.setattr("src.replay_model.history_features",lambda *args:{"price_change_1h":5,"price_change_4h":5,"volume_z":4,"volatility_compression":0.6,"funding_z":-2})


def test_selected_later_is_a_real_system_warning_not_a_selection_miss(complete_features):
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    market=[{"symbol":"A","time":(start+timedelta(minutes=5*i)).isoformat(),"close":140 if i>=12 else 100} for i in range(301)]
    result=replay([frame(start.isoformat(),False),frame((start+timedelta(minutes=6)).isoformat())],market)
    assert result["metrics"]["model"]["tp"]==result["metrics"]["system"]["tp"]==1
    assert result["metrics"]["selection_misses"]==0
    assert result["metrics"]["system"]["matches"][0]["lead_hours"]==0.9


def test_replay_orders_absolute_time_and_rejects_duplicate_or_future_inputs(complete_features):
    a=frame("2026-01-01T00:45:00+00:00")
    b=frame("2026-01-01T08:30:00+08:00")
    report=replay([a,b],[])
    assert report["predictions"][0]["time"].hour==0 and report["predictions"][0]["time"].minute==30
    with pytest.raises(ValueError,match="duplicate"):
        replay([a,a],[])
    a["snapshot"]["observed_at"]="2026-01-01T00:46:00+00:00"
    with pytest.raises(ValueError,match="not available"):
        replay([a],[])


def test_optimized_event_windows_preserve_known_pump_and_dump_crossings():
    start=datetime(2026,1,1,tzinfo=timezone.utc)
    rows=[{"symbol":"A","time":start+timedelta(minutes=5*i),"close":140 if 12<=i<312 else 100} for i in range(601)]
    events,eligible=detect_events(rows)
    assert len(eligible)==313
    assert [(e["direction"],e["event_time"]) for e in events]==[("LONG",start+timedelta(hours=1)),("SHORT",start+timedelta(hours=26))]
