"""Read-only, bounded historical smoke dataset. Not full-market accuracy evidence."""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.live_collector import _get
from src.request_budget import RequestBudget,CURRENT
from src.replay_model import replay
from src.historical_dataset import frames_from_dataset,market_from_dataset


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--symbols",nargs="+",required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if not 1<=len(args.symbols)<=3 or len(set(args.symbols))!=len(args.symbols):
        raise ValueError("select one to three distinct cohort symbols")
    now=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    dataset={"captured_as_of":now.isoformat(),"evaluation_start":(now-timedelta(hours=72)).isoformat(),
        "evaluation_end":(now-timedelta(hours=24)).isoformat(),"symbols":{},"scope":"fixed small cohort; not full Binance universe",
        "availability_assumption":"historical upstream timestamps treated as available at that instant; actual receipt latency cannot be reconstructed"}
    budget=RequestBudget(seconds=40,max_requests=30)
    token=CURRENT.set(budget)
    try:
        for symbol in args.symbols:
            source={"klines":[],"oi":[],"funding":[]}
            for name,path,start,limit,step,field,extra in (
                ("klines","/fapi/v1/klines",now-timedelta(days=8),1500,300000,0,{"interval":"5m"}),
                ("oi","/futures/data/openInterestHist",now-timedelta(days=4),500,300000,"timestamp",{"period":"5m"}),
                ("funding","/fapi/v1/fundingRate",now-timedelta(days=10),1000,1,"fundingTime",{})):
                cursor=int(start.timestamp()*1000);end=int(now.timestamp()*1000)
                while cursor<=end:
                    # OI history may return the latest limit rows for a broad
                    # range. Bound each page's time window to prevent silently
                    # losing the earliest context.
                    page_end=min(end,cursor+(limit-1)*step) if name=="oi" else end
                    rows=_get(path,dict(extra,symbol=symbol,startTime=cursor,endTime=page_end,limit=limit))
                    if not rows:break
                    source[name].extend(rows)
                    next_cursor=int(rows[-1][field])+step
                    if next_cursor<=cursor:raise ValueError("non-advancing historical response")
                    cursor=next_cursor
                    if len(rows)<limit and name!="oi":break
            dataset["symbols"][symbol]=source
    finally:
        CURRENT.reset(token)
    args.output.mkdir(parents=True,exist_ok=True)
    raw=args.output/"cohort.json"
    raw.write_text(json.dumps(dataset,separators=(",",":"))+"\n",encoding="utf-8")
    import hashlib
    report=replay(list(frames_from_dataset(dataset)),list(market_from_dataset(dataset)))
    report.update(scope=dataset["scope"],availability_assumption=dataset["availability_assumption"],input_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),request_metrics=budget.report())
    report["evaluated_frames"]=sum("quality" not in prediction for prediction in report["predictions"])
    (args.output/"replay.json").write_text(json.dumps(report,default=lambda v:v.isoformat(),indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"source":str(raw),"frames":len(report["predictions"]),"evaluated_frames":report["evaluated_frames"],"metrics":report["metrics"],"requests":budget.report()},indent=2))
    if report["evaluated_frames"]!=len(report["predictions"]):
        raise SystemExit("historical cohort has incomplete evaluation context; inspect replay quality")


if __name__=="__main__":main()
