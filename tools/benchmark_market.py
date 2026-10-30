"""Small read-only market benchmark; never invokes scans or writes remote state."""
import argparse
import json
import sys
import time
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.live_collector import collect_market
from src.live_features import derive_live_features
from src.request_budget import RequestBudget,CURRENT

parser=argparse.ArgumentParser()
parser.add_argument("--symbols",nargs="+",required=True)
parser.add_argument("--output",type=Path,required=True)
args=parser.parse_args()
if len(args.symbols)>3:
    raise SystemExit("benchmark is deliberately limited to three symbols")
budget=RequestBudget(seconds=25,max_requests=16)
token=CURRENT.set(budget)
start=time.monotonic()
result={"observed_at":datetime.now(timezone.utc).isoformat(),"read_only":True,"execution_location":"local workstation, not Vercel sin1","symbols":args.symbols}
try:
    snapshots=collect_market(symbols=args.symbols)
    result["observations"]=len(snapshots)
    result["market_errors"]=[s["symbol"] for s in snapshots if s.get("error")]
    result["feature_availability"]={s["symbol"]:{k:v is not None for k,v in derive_live_features(s["symbol"]).items() if k in {"price_change_1h","price_change_4h","funding_z","volume_z","volatility_compression"}} for s in snapshots if not s.get("error")}
except Exception as exc:
    result.update(error_type=type(exc).__name__,http_status=getattr(exc,"code",None))
finally:
    CURRENT.reset(token)
    result.update(duration_seconds=round(time.monotonic()-start,3),metrics=budget.report())
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
print(json.dumps(result,indent=2))
