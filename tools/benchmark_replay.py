"""Deterministic offline scale check, not a model-accuracy benchmark."""
import json
import sys
import time
import tracemalloc
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.event_evaluation import detect_events

start=datetime(2026,1,1,tzinfo=timezone.utc)
rows=[{"symbol":f"S{symbol:02}","time":start+timedelta(minutes=5*i),"close":100+(i%13)/100} for symbol in range(10) for i in range(8640)]
tracemalloc.start();began=time.perf_counter()
events,eligible=detect_events(rows)
elapsed=time.perf_counter()-began
_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
report={"synthetic":True,"rows":len(rows),"symbols":10,"days_per_symbol":30,"eligible_windows":len(eligible),"events":len(events),"seconds_with_tracemalloc":round(elapsed,3),"additional_peak_mib":round(peak/1024**2,2)}
out=Path(__file__).resolve().parents[1]/"artifacts/verification/replay-performance.json"
out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,indent=2))
