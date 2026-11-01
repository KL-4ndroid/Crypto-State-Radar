"""Explicit bounded BSC read-only worker. No wallet, signing or trading commands."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.research_journal import open_journal
from src.bsc.scanner import run,stream_for
from src.bsc.scanner import publish_current,ChainReorganization
from src.record_stream import StreamCapacity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['status','once','watch','publish'])
    parser.add_argument('--root',default='data/bsc/journal')
    parser.add_argument('--runtime',type=Path,help='Ignored environment JSON; never print credentials')
    parser.add_argument('--start-block',type=int)
    parser.add_argument('--max-blocks',type=int,choices=[1,2,3],default=1)
    parser.add_argument('--cycles',type=int,default=1,help='Finite watch count; no implicit background process')
    parser.add_argument('--interval',type=int,default=30)
    parser.add_argument('--catchup-interval',type=int,default=5)
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    if not 1<=args.cycles<=100 or not 10<=args.interval<=3600 or not 3<=args.catchup_interval<=60:
        parser.error('cycles 1..100; interval 10..3600 seconds')
    if args.runtime:
        allowed={'CSR_RESEARCH_DATABASE_URL','CSR_RESEARCH_ENVIRONMENT','CSR_STATE_KEY',
                 'KV_REST_API_URL','KV_REST_API_TOKEN','CSR_BSC_RPC_URLS','CSR_BSC_STREAM',
                 'CSR_BSC_DISCOVERY_MODE','CSR_BSC_PUBLISH_URL','CSR_WORKER_TOKEN','CSR_BSC_PREVIEW_OIDC'}
        allowed.add('CSR_BSC_WATCH_ADDRESSES')
        runtime=json.loads(args.runtime.read_text(encoding='utf-8'))
        os.environ.update({k:str(v) for k,v in runtime.items() if k in allowed})
    journal=open_journal(args.root)
    try:
        if args.command=='status':
            stream=stream_for(journal);revision,payload=stream.load()
            print(json.dumps({'revision':revision,'cursor':payload.get('cursor'),
                'projects':len(payload.get('projects',{})),'capacity':stream.stats(),'execution_enabled':False}));return
        if args.command=='publish':
            print(json.dumps({'published':publish_current(journal),'revision':stream_for(journal).load()[0]}));return
        lock=None
        if args.command=='watch':
            import sqlite3
            lock_path=Path(args.root)/'worker-lock.sqlite3';lock_path.parent.mkdir(parents=True,exist_ok=True)
            lock=sqlite3.connect(str(lock_path),timeout=.2)
            lock.execute('BEGIN EXCLUSIVE')
        for index in range(args.cycles if args.command=='watch' else 1):
            if (Path(args.root)/'WATCH_STOP').exists():
                print(json.dumps({'status':'stopped_by_file'}),flush=True);break
            for attempt in range(3):
                try:
                    # Recover the committed read view before fetching more data.
                    if args.publish and stream_for(journal).load()[0]:publish_current(journal)
                    result=run(journal,max_blocks=args.max_blocks,
                        start_block=args.start_block if index==0 else None,publish=args.publish)
                    break
                except (ChainReorganization,StreamCapacity):raise
                except (RuntimeError,TimeoutError) as exc:
                    print(json.dumps({'status':'retry_wait','error_type':type(exc).__name__,
                        'attempt':attempt+1,'observed_at':datetime.now(timezone.utc).isoformat()}),flush=True)
                    if args.command!='watch' or attempt==2:raise
                    time.sleep(5*2**attempt)
            print(json.dumps(dict(result,cycle=index+1,max_cycles=args.cycles if args.command=='watch' else 1)),flush=True)
            if args.command=='watch' and index+1<args.cycles:
                time.sleep(args.catchup_interval if result['lag_blocks']>128 else args.interval)
    finally:
        if 'lock' in locals() and lock:lock.close()
        journal.close()


if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('BSC worker stopped: '+type(exc).__name__) from None
