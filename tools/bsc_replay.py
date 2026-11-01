"""Export/replay saved BSC evidence. Never fetches chain data or submits orders."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.bsc.replay import replay
from src.bsc.scanner import stream_for
from src.research_journal import open_journal


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='data/bsc/journal')
    parser.add_argument('--runtime',type=Path)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('output already exists; preserve research evidence')
    if args.input:
        rows=json.loads(args.input.read_text(encoding='utf-8'))['evidence']
    else:
        if args.runtime:
            config=json.loads(args.runtime.read_text(encoding='utf-8'))
            os.environ.update({k:str(v) for k,v in config.items() if k in {'CSR_RESEARCH_DATABASE_URL','CSR_RESEARCH_ENVIRONMENT','CSR_BSC_STREAM'}})
        journal=open_journal(args.root)
        try:
            stream=stream_for(journal)
            rows=[json.loads(r[0]) for r in journal.sql('SELECT payload FROM csr_records WHERE kind=? AND (record_key LIKE ? OR record_key LIKE ? OR record_key LIKE ?) ORDER BY record_key',(stream.kind,'block:%','code:%','range:%')).fetchall()]
        finally:journal.close()
    report={'evidence':rows,'replay':replay(rows)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as output:output.write(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'blocks':report['replay']['blocks'],'projects':len(report['replay']['projects']),'network_requests':0,'execution_enabled':False}))


if __name__=='__main__':main()
