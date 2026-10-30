"""Small operational receipts; never log tokens, DSNs or market payloads."""
import json
from datetime import datetime, timezone


def emit_operation(handler, operation, http_status, *, result=None, elapsed=0, error=None):
    source=handler.headers.get("X-Radar-Trigger", "direct")
    if source not in {"schedule","workflow_dispatch","direct"}:source="unknown"
    run_id=handler.headers.get("X-Radar-Run-Id", "")
    event={"event":"radar_operation","operation":operation,"http_status":http_status,
        "trigger":source,"completed_at":datetime.now(timezone.utc).isoformat(),"duration_seconds":round(elapsed,3)}
    if run_id.isdigit() and len(run_id)<=24:event["workflow_run_id"]=run_id
    if (result or {}).get("mode") in {"live","shadow"}:event["mode"]=result["mode"]
    for key in ("revision","scanned","enrichment_succeeded","enrichment_errors","pending","snapshot_published","published"):
        value=(result or {}).get(key)
        if isinstance(value,(int,float,bool)):event[key]=value
    for key in ("errors","validated"):
        value=(result or {}).get(key)
        if isinstance(value,list):event[key+"_count"]=len(value)
        elif isinstance(value,int):event[key+"_count"]=value
    if error:event["error_type"]=type(error).__name__
    print(json.dumps(event,separators=(",",":")),flush=True)
