"""Compare pinned main's actual feed/engine with current deterministic cases."""
import argparse
import ast
import json
import subprocess
import sys
import types
from pathlib import Path
from datetime import datetime,timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.model_contract import snapshot_features,MODEL_VERSION
from src.radar_engine import classify
from src.state_machine import SymbolState,transition

BASE = "5816d3a3806c2f68c2554f21b407bb620e278b2d"


def replay():
    def source(path):
        return subprocess.check_output(["git","show",f"{BASE}:{path}"],cwd=ROOT,text=True,encoding="utf-8")
    legacy = types.ModuleType("radar_pinned_baseline")
    sys.modules[legacy.__name__] = legacy
    exec(compile(source("src/radar_engine.py"),"pinned_engine","exec"),legacy.__dict__)
    # Load only the feature adapter's pure functions, avoiding network/store imports.
    tree = ast.parse(source("src/run_cycle.py"))
    pure = ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {"_safe_float","_features"}],type_ignores=[])
    feed = {"Any":object}
    exec(compile(pure,"pinned_feed","exec"),feed)
    cases = json.loads((ROOT/"tests/fixtures/market_cases.json").read_text())
    now = datetime(2026,10,30,tzinfo=timezone.utc)
    results = []
    for c in cases:
        if c.get("error"):
            results.append({"case":c["name"],"before":"deferred_api_error","after":"pending_api_error"}); continue
        snap = dict(symbol="TEST",price=c["price"],open_interest=100,**{f"oi_change_{h}h":c[f"oi{h}"] for h in (1,4,24)})
        old = legacy.classify("TEST",feed["_features"](snap),now).to_dict()
        features = snapshot_features(snap)
        features.update(price_change_1h=c.get("p1"),price_change_4h=c.get("p4"),volume_z=c.get("volume_z"),
            volatility_compression=c.get("compression"),funding_z=c.get("funding_z"),funding_acceleration=c.get("funding_acceleration"))
        new = classify("TEST",features,now).to_dict()
        after = new["state"]
        if c.get("previous"):
            state,_ = transition(SymbolState("TEST",state=c["previous"],direction="LONG",ready_price=100,entered_at=now.isoformat(),expires_at="2026-10-30T04:00:00+00:00"),new,now)
            after = state.state
        results.append({"case":c["name"],"before":old["state"],"before_scores":[old["pump_score"],old["dump_score"]],
            "after":after,"after_scores":[new["pump_score"],new["dump_score"]],"input":features})
    return {"baseline_commit":BASE,"current_model":MODEL_VERSION,"synthetic":True,"results":results}


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(replay(),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(str(args.output.resolve()))
