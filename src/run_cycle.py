"""Revisioned full observation -> bounded enrichment -> transactional research cycle."""
from __future__ import annotations
import os
import json
import hashlib
import time
from uuid import uuid4
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
try:
    from .live_collector import collect_market
    from .live_features import derive_live_features
    from .model_contract import snapshot_features, number, MODEL_VERSION, model_manifest
    from .radar_engine import classify
    from .state_machine import SymbolState, transition, defer_state, encode_snapshot, decode_snapshot
    from .state_store import state_store_from_env
    from .research_journal import open_journal
    from .scan_plan import plan_scan, drain_candidates
    from .retry_policy import retry_at
    from .request_budget import CURRENT, RequestBudget
except ImportError:
    from live_collector import collect_market
    from live_features import derive_live_features
    from model_contract import snapshot_features, number, MODEL_VERSION, model_manifest
    from radar_engine import classify
    from state_machine import SymbolState, transition, defer_state, encode_snapshot, decode_snapshot
    from state_store import state_store_from_env
    from research_journal import open_journal
    from scan_plan import plan_scan, drain_candidates
    from retry_policy import retry_at
    from request_budget import CURRENT, RequestBudget

PRIORITY = {"EVAAUSDT", "ONUSDT", "PREDICTUSDT"}
SIGNAL_STATES = {"EARLY_WARNING", "PUMP_PRESSURE", "DUMP_PRESSURE", "LONG_READY", "SHORT_READY"}
NOTIFY_STATES = {"LONG_READY", "SHORT_READY", "TRIGGERED", "INVALIDATED", "EXPIRED"}
ENRICHMENT_LIMIT = 25
DRAIN_LIMIT = 100
ENRICHMENT_WORKERS = 8
_safe_float = number
_features = snapshot_features


def _enrichment_candidates(snapshots, previous, now):
    return plan_scan(snapshots, previous, {}, now, ENRICHMENT_LIMIT)[0]


def run(symbols=None, root="data/journal", drain=False, operation_id=None):
    started_at, started = datetime.now(timezone.utc), time.monotonic()
    budget = RequestBudget()
    token = CURRENT.set(budget)
    journal = None
    try:
        journal = open_journal(root)
        revision, saved = journal.load()
        if operation_id:
            receipt = journal.record("scheduler_operations", operation_id)
            if receipt is not None:
                return dict(receipt, deduplicated=True)
        lifecycle = state_store_from_env(load=saved.get("states") is None)
        mode = os.getenv("CSR_SCAN_MODE", "shadow" if os.getenv("VERCEL") else "live")
        if mode not in {"live", "shadow"}:
            raise ValueError("CSR_SCAN_MODE must be live or shadow")
        if os.getenv("VERCEL_ENV") not in {None,"production"} and mode=="live" and os.getenv("CSR_STATE_KEY","") in {"","crypto-state-radar:state:v1"}:
            raise RuntimeError("preview publication requires an isolated CSR_STATE_KEY")
        previous = decode_snapshot(saved) if saved.get("states") is not None else lifecycle.all()
        queue = saved.get("pending", {})
        capacity=DRAIN_LIMIT if drain else ENRICHMENT_LIMIT
        if drain:
            # A bounded continuation avoids fetching full-market OI a second time.
            symbols,queue = drain_candidates(previous,queue,datetime.now(timezone.utc),capacity)
            if not symbols:
                retry_times=[item["next_attempt_at"] for item in queue.values() if item.get("next_attempt_at")]
                return {"mode":mode,"status":"waiting" if queue else "idle","pending":len(queue),
                    "next_retry_at":min(retry_times) if retry_times else None,"completed_at":datetime.now(timezone.utc).isoformat()}
        snapshots = collect_market(symbols=symbols)
        collected_at = datetime.now(timezone.utc)
        candidates, queue, overdue = plan_scan(snapshots,previous,queue,collected_at,capacity)
        def enrich(snap):
            CURRENT.set(budget)
            try:
                extra = derive_live_features(snap["symbol"])
                error = "insufficient_price_history" if any(number(extra.get(k)) is None for k in ("price_change_1h", "price_change_4h")) else None
                return snap["symbol"],(extra,error)
            except Exception as exc:
                return snap["symbol"],({},type(exc).__name__)
        with ThreadPoolExecutor(max_workers=ENRICHMENT_WORKERS) as executor:
            enriched = dict(executor.map(enrich,candidates))
        now = datetime.now(timezone.utc)
        states = dict(previous)
        run_id = str(uuid4())
        records = [("models",MODEL_VERSION,model_manifest())]
        summary = {"run_id":run_id,"mode":mode,"model_version":MODEL_VERSION,"started_at":started_at.isoformat(),
            "scan_policy":"scan-v2-fair-retry","capacity":capacity,"drain":drain,
            "collected_at":collected_at.isoformat(),"observed_at":now.isoformat(),"evaluated_at":now.isoformat(),
            "scanned":0,"errors":0,"signals":0,"transitions":0,"priority":[],"notifications":[],
            "enrichment_selected":len(candidates),"enrichment_succeeded":0,"enrichment_errors":0,
            "enrichment_failures":[],"overdue_symbols":overdue}
        for snap in snapshots:
            symbol = snap.get("symbol","UNKNOWN")
            records.append(("observations",run_id+":"+symbol,dict(snap,run_id=run_id)))
            old = previous.get(symbol,SymbolState(symbol))
            error = "market_api_error" if snap.get("error") else None
            if not error:
                summary["scanned"] += 1
            else:
                summary["errors"] += 1
            if symbol not in enriched and not error:
                states[symbol] = defer_state(old,now,"awaiting_deep_scan")
                continue
            extra, enrichment_error = enriched.get(symbol,({},None))
            error = error or enrichment_error
            features = snapshot_features(snap)
            if not error and (features["price"] is None or features["price"]<=0 or features["open_interest"] is None or features["open_interest"]<=0
                    or any(features.get(k) is None for k in ("oi_change_1h","oi_change_4h","oi_change_24h"))):
                error = "insufficient_market_data"
            queue.setdefault(symbol,{"due_since":now.isoformat(),"reason":"retry"})
            queue[symbol]["last_attempt"] = now.isoformat()
            if error:
                attempts=queue[symbol].get("attempts",0)+1
                queue[symbol].update(attempts=attempts,next_attempt_at=retry_at(now,attempts,symbol),last_error=error)
                states[symbol] = defer_state(old,now,error)
                summary["enrichment_errors"] += int(symbol in enriched)
                summary["enrichment_failures"].append({"symbol":symbol,"reason":error})
                records.append(("evaluations",run_id+":"+symbol,{"symbol":symbol,"run_id":run_id,"status":"pending","reason":error}))
                continue
            features.update(extra)
            features["observed_at"] = snap.get("observed_at")
            decision = classify(symbol,features,now=now).to_dict()
            state,event = transition(old,decision,now)
            states[symbol] = state
            if state.evaluation_status=="pending":
                reason=state.pending_reason or "state_requires_review"
                attempts=queue[symbol].get("attempts",0)+1
                queue[symbol].update(attempts=attempts,next_attempt_at=retry_at(now,attempts,symbol),last_error=reason)
                summary["enrichment_errors"]+=1
                summary["enrichment_failures"].append({"symbol":symbol,"reason":reason})
                records.append(("evaluations",run_id+":"+symbol,dict(decision,run_id=run_id,status="pending",reason=reason)))
                continue
            queue.pop(symbol,None)
            summary["enrichment_succeeded"] += 1
            records.append(("evaluations",run_id+":"+symbol,dict(decision,run_id=run_id,status=state.evaluation_status)))
            if event and state.state in SIGNAL_STATES:
                signal = dict(decision,entry_price=features["price"],cycle=state.cycle,lifecycle_state=state.state,run_id=run_id)
                identity = f"{symbol}|{now.isoformat()}|{MODEL_VERSION}|{state.state}|{state.cycle}"
                signal["signal_id"] = hashlib.sha256(identity.encode()).hexdigest()[:32]
                records.append(("signals",signal["signal_id"],signal))
                summary["signals"] += 1
            if event:
                event.update(price=features["price"],reasons=decision["reasons"],run_id=run_id)
                records.append(("transitions",run_id+":"+symbol,event))
                summary["transitions"] += 1
                if state.state in NOTIFY_STATES or old.state in NOTIFY_STATES:
                    summary["notifications"].append(event)
            if symbol in PRIORITY:
                summary["priority"].append({"symbol":symbol,"state":state.state,"pump_score":state.pump_score,"dump_score":state.dump_score})
        # Preserve work missing from a subset/drain result and mark it unconfirmed.
        for symbol in queue:
            if symbol in previous and symbol not in {s.get("symbol") for s in snapshots}:
                states[symbol] = defer_state(previous[symbol],now,"awaiting_deep_scan")
        summary.update(pending=len(queue),request_metrics=budget.report(),processed_at=datetime.now(timezone.utc).isoformat())
        records.append(("runs",run_id,summary.copy()))
        if operation_id:
            # Receipt and state commit atomically. Lost HTTP responses must not
            # create a second scan or duplicate signals when delivery retries.
            records.append(("scheduler_operations",operation_id,dict(summary,revision=revision+1)))
        # Continuations must not make a missed full-market scan look fresh.
        previous_full=saved.get("last_full_scan")
        if previous_full is None and saved.get("last_scan") and not saved["last_scan"].get("drain",True):
            previous_full=saved["last_scan"]
        payload = dict(encode_snapshot(states),pending=queue,last_scan=summary,
            last_full_scan=previous_full if drain else summary.copy(),model_version=MODEL_VERSION)
        payload = journal.commit_cycle(revision,payload,records)
        # DB is authoritative research ledger; Upstash is its revisioned read view.
        summary["revision"] = payload["revision"]
        summary["snapshot_published"] = False
        if mode == "live":
            try:
                if not hasattr(lifecycle,"publish_snapshot"):
                    raise RuntimeError("state adapter does not support revisioned publication")
                summary["snapshot_published"] = lifecycle.publish_snapshot(payload)
            except Exception as exc:
                summary["publication_error"] = type(exc).__name__
                # Publication is recoverable from the committed outbox.
        summary.update(completed_at=datetime.now(timezone.utc).isoformat(),duration_seconds=round(time.monotonic()-started,3))
        return summary
    finally:
        CURRENT.reset(token)
        if journal:
            journal.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--drain",action="store_true")
    parser.add_argument("--root",default="data/journal")
    args = parser.parse_args()
    print(json.dumps(run(root=args.root,drain=args.drain),ensure_ascii=False,indent=2))
