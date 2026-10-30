from datetime import datetime, timezone, timedelta
from src.event_evaluation import detect_events, match_events


def test_event_denominator_includes_unselected_symbols_and_excludes_tail():
    start = datetime(2026,1,1,tzinfo=timezone.utc)
    rows = [{"symbol":symbol,"time":start+timedelta(minutes=5*i),"close":140 if i>=12 else 100} for symbol in ("A","B") for i in range(301)]
    events,eligible = detect_events(rows)
    assert len(events)==2
    assert ("A",start+timedelta(hours=24)) not in eligible
    warnings = [{"signal_id":s,"setup_id":0,"symbol":s,"direction":"LONG","time":start,"selected":s=="A"} for s in ("A","B")]
    # Escalation of A's same setup must not inflate the forecast denominator.
    warnings.append(dict(warnings[0],time=start+timedelta(minutes=5)))
    result = match_events(events,warnings,eligible)
    assert result["model"]["recall"]==1
    assert result["system"]["recall"]==0.5
    assert result["selection_misses"]==1
    assert result["system"]["fp"]==0


def test_gap_and_unmatured_window_cannot_be_negative_examples():
    start = datetime(2026,1,1,tzinfo=timezone.utc)
    rows = [{"symbol":"A","time":start+timedelta(minutes=5*i),"close":100} for i in range(289)]
    del rows[10]
    assert detect_events(rows)==([],set())


def test_off_grid_signal_and_later_selected_episode_are_evaluated():
    start = datetime(2026,1,1,tzinfo=timezone.utc)
    rows=[{"symbol":"A","time":start+timedelta(minutes=5*i),"close":140 if i>=12 else 100} for i in range(301)]
    events,eligible=detect_events(rows)
    warnings=[{"symbol":"A","direction":"LONG","setup_id":0,"time":start+timedelta(minutes=m),"selected":selected} for m,selected in ((1,False),(6,True))]
    result=match_events(events,warnings,eligible)
    assert result["model"]["tp"]==result["system"]["tp"]==1
