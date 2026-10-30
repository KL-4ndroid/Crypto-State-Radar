"""Offline V2.4 replay and event metrics from explicitly supplied raw frames.

Frames JSONL: {as_of, snapshot, klines, funding, selected}. Market JSONL:
{symbol,time,close}, where time is the close's availability time. Market input
must cover the complete evaluated universe, not just deep-selected symbols.
"""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime
from .model_contract import history_features, snapshot_features, MODEL_VERSION, model_manifest
from .radar_engine import classify
from .state_machine import SymbolState,transition
from .event_evaluation import detect_events,match_events
from .retry_policy import utc


def replay(frames,market):
    states, system_states, predictions, warnings = {},{},[],[]
    seen=set()
    for frame in sorted(frames,key=lambda f:(utc(f["as_of"]),f["snapshot"]["symbol"])):
        now = utc(frame["as_of"])
        if now.tzinfo is None:
            raise ValueError("replay as_of must include timezone")
        snap = frame["snapshot"]; symbol=snap["symbol"]
        identity=(symbol,now)
        if identity in seen:
            raise ValueError("duplicate symbol/as_of frame")
        seen.add(identity)
        observed=utc(snap["observed_at"])
        if observed>now:
            raise ValueError("snapshot was not available at replay as_of")
        if (now-observed).total_seconds()>600:
            predictions.append({"symbol":symbol,"time":now,"quality":"stale_snapshot"});continue
        if snap.get("error"):
            predictions.append({"symbol":symbol,"time":now,"quality":"market_api_error"}); continue
        features = snapshot_features(snap)
        extra = history_features(frame.get("klines",[]),frame.get("funding",[]),now)
        features.update(extra)
        required = ("price","open_interest","oi_change_1h","oi_change_4h","oi_change_24h","price_change_1h","price_change_4h")
        if any(features.get(k) is None for k in required) or features["price"]<=0 or features["open_interest"]<=0:
            predictions.append({"symbol":symbol,"time":now,"quality":"insufficient_data"}); continue
        decision=classify(symbol,features,now).to_dict()
        old=states.get(symbol,SymbolState(symbol))
        state,event=transition(old,decision,now)
        states[symbol]=state
        selected=frame.get("selected",True)
        system_state=system_states.get(symbol,SymbolState(symbol))
        system_event=None
        if selected:
            system_state,system_event=transition(system_state,decision,now)
            system_states[symbol]=system_state
        predictions.append(dict(decision,lifecycle_state=state.state,time=now,selected=selected,
            system_lifecycle_state=system_state.state,system_evaluated=selected))
        for stream,current,current_event in (("model",state,event),("system",system_state,system_event)):
            if current_event and current.state in {"EARLY_WARNING","PUMP_PRESSURE","DUMP_PRESSURE","LONG_READY","SHORT_READY"}:
                sid=hashlib.sha256(f"{stream}|{symbol}|{now.isoformat()}|{MODEL_VERSION}|{current.cycle}".encode()).hexdigest()[:32]
                warnings.append({"signal_id":sid,"symbol":symbol,"direction":"LONG" if current.direction.startswith("LONG") else "SHORT",
                    "time":now,"setup_id":current.cycle,"selected":selected,"stream":stream})
    rows=[dict(r,time=utc(r["time"])) for r in market]
    events,eligible=detect_events(rows)
    return {"model_version":MODEL_VERSION,"model_manifest":model_manifest(),"predictions":predictions,"events":events,"metrics":match_events(events,warnings,eligible),
        "limitations":["5m-close event definition","boundary precision is five minutes","input universe completeness must be established externally"]}


def main():
    parser=argparse.ArgumentParser()
    inputs=parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--frames",type=Path)
    inputs.add_argument("--dataset",type=Path)
    parser.add_argument("--market",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    read=lambda path:[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.dataset:
        from .historical_dataset import frames_from_dataset,market_from_dataset
        dataset=json.loads(args.dataset.read_text(encoding="utf-8"))
        report=replay(list(frames_from_dataset(dataset)),list(market_from_dataset(dataset)))
        report.update(scope=dataset.get("scope"),availability_assumption=dataset.get("availability_assumption"))
        paths=(args.dataset,)
    else:
        if not args.market:parser.error("--market is required with --frames")
        report=replay(read(args.frames),read(args.market))
        paths=(args.frames,args.market)
    report["input_hashes"]={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,default=lambda v:v.isoformat(),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
