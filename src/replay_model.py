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
from .model_contract import history_features, snapshot_features, MODEL_VERSION
from .radar_engine import classify
from .state_machine import SymbolState,transition
from .event_evaluation import detect_events,match_events


def replay(frames,market):
    states, predictions, warnings = {},[],[]
    for frame in sorted(frames,key=lambda f:(f["as_of"],f["snapshot"]["symbol"])):
        now = datetime.fromisoformat(frame["as_of"].replace("Z","+00:00"))
        if now.tzinfo is None:
            raise ValueError("replay as_of must include timezone")
        snap = frame["snapshot"]; symbol=snap["symbol"]
        if snap.get("error"):
            predictions.append({"symbol":symbol,"time":now,"quality":"market_api_error"}); continue
        features = snapshot_features(snap)
        extra = history_features(frame.get("klines",[]),frame.get("funding",[]),now)
        features.update(extra)
        required = ("price","open_interest","oi_change_1h","oi_change_4h","oi_change_24h","price_change_1h","price_change_4h")
        if any(features.get(k) is None for k in required):
            predictions.append({"symbol":symbol,"time":now,"quality":"insufficient_data"}); continue
        decision=classify(symbol,features,now).to_dict()
        old=states.get(symbol,SymbolState(symbol))
        state,event=transition(old,decision,now)
        states[symbol]=state
        predictions.append(dict(decision,lifecycle_state=state.state,time=now,selected=frame.get("selected",True)))
        if event and state.state in {"EARLY_WARNING","PUMP_PRESSURE","DUMP_PRESSURE","LONG_READY","SHORT_READY"}:
            sid=hashlib.sha256(f"{symbol}|{now.isoformat()}|{MODEL_VERSION}|{state.cycle}".encode()).hexdigest()[:32]
            warnings.append({"signal_id":sid,"symbol":symbol,"direction":"LONG" if state.direction.startswith("LONG") else "SHORT",
                "time":now,"setup_id":state.cycle,"selected":frame.get("selected",True)})
    rows=[dict(r,time=datetime.fromisoformat(r["time"].replace("Z","+00:00"))) for r in market]
    events,eligible=detect_events(rows)
    return {"model_version":MODEL_VERSION,"predictions":predictions,"events":events,"metrics":match_events(events,warnings,eligible),
        "limitations":["5m-close event definition","boundary precision is five minutes","input universe completeness must be established externally"]}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--frames",type=Path,required=True)
    parser.add_argument("--market",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    read=lambda path:[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    report=replay(read(args.frames),read(args.market))
    report["input_hashes"]={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.frames,args.market)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,default=lambda v:v.isoformat(),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
