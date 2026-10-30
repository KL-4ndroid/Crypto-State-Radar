"""Read-only 24-hour evidence report; does not call Binance or trigger jobs."""
import argparse
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import psycopg
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.operational_acceptance import analyze
from src.retry_policy import utc
from src.scheduler_contract import CRONS


def collect(runtime,qstash,start,end,now):
    rows={}
    with psycopg.connect(runtime['CSR_RESEARCH_DATABASE_URL'],autocommit=True,connect_timeout=8) as db:
        with db.transaction():
            db.execute('SET TRANSACTION READ ONLY')
            db.execute('SET LOCAL statement_timeout=5000')
            revision,payload,label=db.execute('SELECT revision,payload,environment FROM csr_runtime WHERE id=1').fetchone()
            if label!='production':raise ValueError('acceptance requires the production ledger')
            for kind,field in [('runs','processed_at'),('scheduler_receipts','started_at')]:
                # Fixed field names, parameterized values; no state mutation.
                query="SELECT payload FROM csr_records WHERE kind=%s AND (payload::jsonb->>'"+field+"')::timestamptz >=%s AND (payload::jsonb->>'"+field+"')::timestamptz <%s"
                rows[kind]=[json.loads(r[0]) for r in db.execute(query,(kind,start-timedelta(minutes=5),min(end+timedelta(minutes=5),now))).fetchall()]
            validation_counts=dict(db.execute('SELECT status,COUNT(*) FROM csr_validation_jobs GROUP BY status').fetchall())
    provider=urlparse(qstash['QSTASH_URL'])
    if provider.scheme!='https' or not provider.hostname.endswith('.upstash.io'):raise ValueError('unexpected provider origin')
    base=qstash['QSTASH_URL'].rstrip('/')
    headers={'Authorization':'Bearer '+qstash['QSTASH_TOKEN']}
    events=[];cursor=None;complete=False
    for _ in range(20):
        params={'count':1000,'fromDate':int(start.timestamp()*1000),'toDate':int(min(now,end+timedelta(minutes=5)).timestamp()*1000)}
        if cursor:params['cursor']=cursor
        response=requests.get(base+'/v2/logs',headers=headers,params=params,timeout=20);response.raise_for_status()
        page=response.json()
        # Never persist provider-stored headers, raw request/response bodies or IPs.
        events.extend({k:e.get(k) for k in ('time','messageId','state','scheduleId','responseStatus')} for e in page.get('events',[]))
        next_cursor=page.get('cursor')
        if not next_cursor:complete=True;break
        if next_cursor==cursor:break
        cursor=next_cursor
    response=requests.get(base+'/v2/schedules',headers=headers,timeout=20);response.raise_for_status()
    schedules=response.json()
    expected={'csr-production-v1-'+op.replace('_','-'):(op,cron) for op,cron in CRONS.items()}
    active=[s for s in schedules if not s.get('isPaused',s.get('paused',False)) and s.get('destination')=='https://crypto-state-radar.vercel.app/api/scheduler']
    configurations_match=len(active)==len(expected) and all(s.get('scheduleId') in expected and s.get('cron')==expected[s['scheduleId']][1] for s in active)
    report=analyze(start,end,now,rows['runs'],rows['scheduler_receipts'],events,complete)
    report.update(ledger_revision=revision,validation_job_counts=validation_counts,
        scan_requests_from_run_records=sum(r.get('request_metrics',{}).get('requests',0) for r in rows['runs'] if start<=utc(r['processed_at'])<min(end,now)),
        scheduler_configuration_matches=configurations_match,
        current_pending=len(json.loads(payload).get('pending',{})),
        collection_scope='read-only PostgreSQL and QStash GETs; zero Binance requests')
    if not configurations_match and report['status']=='passed':report['status']='needs_review'
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start',required=True,help='UTC/offset timestamp recorded at acceptance baseline')
    p.add_argument('--hours',type=int,default=24)
    p.add_argument('--runtime',default='.vercel/private/production-runtime.json')
    p.add_argument('--qstash',default='.vercel/private/qstash-runtime.json')
    p.add_argument('--output',default='artifacts/operations/acceptance-24h.json')
    args=p.parse_args()
    if not 1<=args.hours<=48:p.error('hours must be between 1 and 48')
    start=utc(args.start);end=start+timedelta(hours=args.hours);now=datetime.now(timezone.utc)
    report=collect(json.loads(Path(args.runtime).read_text()),json.loads(Path(args.qstash).read_text()),start,end,now)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix('.tmp');temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(output)
    print(json.dumps({k:report[k] for k in ('status','window_start','window_end','full_24_hours_observed','checks','scheduler_configuration_matches','operations','current_pending')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('acceptance collection failed: '+type(exc).__name__) from None
