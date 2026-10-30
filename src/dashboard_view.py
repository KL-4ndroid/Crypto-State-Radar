"""Allowlisted public projection, built once by the existing snapshot writer."""
import hashlib
import json
from datetime import datetime, timezone

SCHEMA_VERSION = 1
FIELDS = ("state", "direction", "cycle", "entered_at", "updated_at",
    "observed_at", "candle_closed_at", "checked_at", "expires_at", "ready_price",
    "ready_oi", "last_price", "last_open_interest", "oi_change_1h", "oi_change_4h",
    "oi_change_24h", "funding_rate", "pump_score", "dump_score", "peak_price",
    "trough_price", "peak_oi", "trough_oi", "evaluation_status", "last_transition", "model_version")
SCAN_FIELDS = ("processed_at", "evaluated_at", "enrichment_succeeded", "pending", "errors", "enrichment_errors", "scanned")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def publication(payload):
    states = {}
    for symbol, source in payload.get("states", {}).items():
        if not isinstance(source, dict):
            continue
        row = {key: source[key] for key in FIELDS if key in source and source[key] is not None and isinstance(source[key], (str, int, float, bool))}
        reasons=source.get("last_reasons") or []
        reasons = [s[:128] for s in reasons if isinstance(s, str)][:16] if isinstance(reasons,list) else []
        if reasons:row["last_reasons"]=reasons
        states[symbol] = row
    scan = payload.get("last_scan") or {}
    summary = {k: scan[k] for k in SCAN_FIELDS if k in scan and isinstance(scan[k], (str, int, float, type(None)))}
    view = {"schema_version": SCHEMA_VERSION, "model_version": payload.get("model_version"), "states": states, "last_scan": summary}
    # Content identity includes the public schema and model, never internal revision.
    snapshot_id = hashlib.sha256(encode(view).encode()).hexdigest()
    published = datetime.now(timezone.utc).isoformat()
    view.update(snapshot_id=snapshot_id, published_at=published)
    checked = scan.get("processed_at") or scan.get("evaluated_at")
    errors = (scan.get("errors") or 0) + (scan.get("enrichment_errors") or 0)
    market = "unknown" if not checked else "partial" if errors else "ok" if scan.get("scanned", 0) else "unknown"
    meta = {"schema_version": SCHEMA_VERSION, "snapshot_id": snapshot_id, "published_at": published,
        "last_full_scan_at": (payload.get("last_full_scan") or {}).get("processed_at"),
        "last_successful_scan_at": checked if scan.get("scanned", 0) and not errors else None,
        "expected_scan_interval_seconds": 3600, "scan_status": market,
        "health_checked_at": checked, "services": {"market": market, "state": "ok"},
        "summary_url": "/api/dashboard-latest"}
    return {"meta": meta, "view": view}


def redis_publication(key, payload):
    """One atomic state+public-view+manifest write, sharing the existing CAS."""
    public = publication(payload)
    script = """local raw=redis.call('GET',KEYS[1]); local rev=0;
    if raw then local old=cjson.decode(raw); rev=tonumber(old.revision or 0);
    local incoming=cjson.decode(ARGV[2]);
    if old.ledger_id and old.ledger_id~=incoming.ledger_id then return redis.error_reply('ledger identity mismatch') end end;
    if rev>tonumber(ARGV[1]) then return 0 end;
    if raw and rev==tonumber(ARGV[1]) then
      local view=redis.call('GET',KEYS[2]); local meta=redis.call('GET',KEYS[3]);
      if view and meta then
        local vok,v=pcall(cjson.decode,view); local mok,m=pcall(cjson.decode,meta);
        local incoming_view=cjson.decode(ARGV[3]); local incoming_meta=cjson.decode(ARGV[4]);
        if vok and mok and type(v)=='table' and type(m)=='table'
          and v.snapshot_id==incoming_view.snapshot_id and m.snapshot_id==v.snapshot_id
          and m.last_full_scan_at==incoming_meta.last_full_scan_at
          and v.schema_version==incoming_view.schema_version and m.schema_version==incoming_meta.schema_version
          then return 1 end
      end
    end;
    redis.call('SET',KEYS[1],ARGV[2]);
    redis.call('SET',KEYS[2],ARGV[3]);
    redis.call('SET',KEYS[3],ARGV[4]); return 1"""
    return ["EVAL", script, 3, key, key+":dashboard-latest", key+":dashboard-meta",
        payload.get("revision", 0), encode(payload), encode(public["view"]), encode(public["meta"])]
