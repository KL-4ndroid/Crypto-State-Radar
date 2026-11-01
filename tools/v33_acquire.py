"""Explicit bounded local pilot; plan is read-only and once performs real requests."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.research_journal import ResearchJournal
from src.v33.archive import FileArchive
from src.v33.journal_archive import JournalArchive
from src.v33.acquisition import install_acquisition
from src.v33.acquisition_sources import SUPPORTED
from src.v33.acquisition_worker import AcquisitionWorker
from src.v33.acquisition_features import derive_measurements
from src.v33.journal import ContractStore,install_local
from src.v33.contracts import stamp
from src.retry_policy import utc


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['plan','once','derive'])
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--symbols',nargs='+',required=True)
    parser.add_argument('--sources',nargs='+',choices=sorted(SUPPORTED),default=['oi','taker','premium_mark_index'])
    parser.add_argument('--start',help='UTC/aware five-minute bootstrap boundary; existing cursors are retained')
    parser.add_argument('--journal',type=Path,default=Path('data/v33/acquisition.sqlite3'))
    parser.add_argument('--archive',type=Path,default=Path('data/v33/raw'))
    parser.add_argument('--archive-backend',choices=['file','journal'],default='file')
    parser.add_argument('--max-requests',type=int,default=12)
    parser.add_argument('--page-limit',type=int,default=500)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 1<=len(set(args.symbols))<=5 or not 1<=args.max_requests<=50 or not 1<=args.page_limit<=500:
        parser.error('expected 1..5 pilot symbols, 1..50 requests, page limit 1..500')
    if args.output and args.output.resolve()==args.journal.resolve():parser.error('output cannot overwrite journal')
    now=datetime.now(timezone.utc)
    start=utc(args.start) if args.start else datetime.fromtimestamp((int(now.timestamp())//300-12)*300,timezone.utc)
    plan={'namespace':'SHADOW','run_id':args.run_id,'symbols':sorted(set(args.symbols)),
        'sources':sorted(set(args.sources)),'new_stream_bootstrap':stamp(start),'max_requests':args.max_requests,
        'page_limit':args.page_limit,'persistent_local_archive':True,'archive_backend':args.archive_backend,'scheduled':False,'production_writes':False}
    if args.command=='plan':report=plan
    else:
        journal=ResearchJournal(args.journal)
        try:
            archive=JournalArchive(journal) if args.archive_backend=='journal' else FileArchive(args.archive)
            install_local(journal);install_acquisition(journal)
            store=ContractStore(journal,'SHADOW',args.run_id)
            report=AcquisitionWorker(store,archive,max_requests=args.max_requests,page_limit=args.page_limit).cycle(args.symbols,args.sources,start) if args.command=='once' else {'network_requested':False}
            features=[]
            for symbol in sorted(set(args.symbols)):
                try:features.append(derive_measurements(store,symbol,datetime.now(timezone.utc),archive))
                except Exception as exc:features.append({'symbol':symbol,'status':'DERIVATION_PENDING','error_type':type(exc).__name__})
            report.update(plan=plan,measurements=features)
        finally:journal.close()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('acquisition command failed: '+type(exc).__name__) from None
