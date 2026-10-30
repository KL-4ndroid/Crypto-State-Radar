"""One full-market shadow cycle using disposable local persistence only."""
import json
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
# Never inherit credentials or a production state location into this benchmark.
for key in ("KV_REST_API_URL","KV_REST_API_TOKEN","CSR_STATE_STORE_URL","CSR_RESEARCH_DATABASE_URL","VERCEL","VERCEL_ENV"):
    os.environ.pop(key,None)
from src.run_cycle import run
from src.research_journal import open_journal
from src.live_collector import active_usdt_perpetuals
from datetime import datetime,timezone
import argparse
parser=argparse.ArgumentParser()
parser.add_argument("--drain-only",action="store_true")
args=parser.parse_args()

with tempfile.TemporaryDirectory(prefix="csr-shadow-benchmark-") as root:
    os.environ.update(CSR_STATE_FILE=str(Path(root)/"state.json"),CSR_SCAN_MODE="shadow")
    if args.drain_only:
        symbols=active_usdt_perpetuals()[:100]
        journal=open_journal(root)
        try:
            journal.commit_cycle(0,{"states":{},"pending":{s:{"due_since":datetime.now(timezone.utc).isoformat(),"reason":"rotation"} for s in symbols}},[])
        finally:journal.close()
    result=run(root=root,drain=args.drain_only)
    report={k:result[k] for k in ("model_version","mode","scanned","errors","enrichment_selected","enrichment_succeeded","enrichment_errors","pending","request_metrics","duration_seconds","snapshot_published")}
    report.update(location="local workstation, not Vercel sin1",production_writes=False)
    report.update(continuation=args.drain_only,seed_universe_requests=1 if args.drain_only else 0)
    name="drain-shadow.json" if args.drain_only else "full-market-shadow.json"
    out=Path(__file__).resolve().parents[1]/"artifacts/verification"/name
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
