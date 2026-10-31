"""Local, explicit V3.3 contract replay. Never calls Binance or publishes signals."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.research_journal import ResearchJournal
from src.v33.contracts import RawObservation,Feature
from src.v33.journal import ContractStore,install_local
from src.v33.registry import defaults
from src.v33.sources import metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['sources','init','replay'])
    parser.add_argument('--journal',type=Path,default=Path('data/v33/research.sqlite3'))
    parser.add_argument('--namespace',choices=['SHADOW','REPLAY','BACKTEST'],default='REPLAY')
    parser.add_argument('--run-id')
    parser.add_argument('--frames',type=Path)
    parser.add_argument('--rule',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.command=='sources':
        print(json.dumps(metadata(),ensure_ascii=False,indent=2));return
    if args.command=='replay' and not (args.run_id and args.frames and args.output):
        parser.error('replay requires --run-id, --frames and --output')
    if args.command=='replay' and args.output.resolve() in {args.frames.resolve(),args.journal.resolve(),*([] if args.rule is None else [args.rule.resolve()])}:
        parser.error('output must not overwrite input, rule or journal files')
    journal=ResearchJournal(args.journal)
    try:
        install_local(journal)
        if args.command=='init':
            print('local V3.3 contract schema initialized; production unchanged');return
        store=ContractStore(journal,args.namespace,args.run_id)
        rule=json.loads(args.rule.read_text(encoding='utf-8')) if args.rule else defaults()
        store.put_rule(rule)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        count=0
        with args.frames.open(encoding='utf-8') as source,args.output.open('w',encoding='utf-8') as output:
            for line in source:
                if not line.strip():continue
                frame=json.loads(line);aliases={}
                for observation in frame.get('observations',[]):
                    item=dict(observation);alias=item.pop('alias')
                    if alias in aliases:raise ValueError('duplicate source alias')
                    aliases[alias]=store.put_raw(RawObservation(**item))
                ids=[]
                for feature in frame.get('features',[]):
                    item=dict(feature)
                    if 'source_aliases' in item:
                        item['source_ids']=tuple(aliases[a] for a in item.pop('source_aliases'))
                    ids.append(store.put_feature(Feature(**item)))
                result=store.evaluate(frame['symbol'],frame['evaluation_time'],rule['rule_version'],ids,
                    supersedes=frame.get('supersedes_evaluation_id'))
                output.write(json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True)+'\n');count+=1
        print(json.dumps({'evaluations':count,'namespace':args.namespace,'run_id':args.run_id,'model_status':'PRE_VALIDATION','binance_requests':0}))
    finally:journal.close()


if __name__=='__main__':main()
