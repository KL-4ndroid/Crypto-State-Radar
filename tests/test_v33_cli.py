import json
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_research_cli_alias_ingestion_idempotency_and_output_guard(tmp_path):
    when='2026-10-30T00:00:00+00:00'
    frames=tmp_path/'frames.jsonl';journal=tmp_path/'research.db';output=tmp_path/'result.jsonl'
    frames.write_text(json.dumps({'symbol':'A','evaluation_time':when,'observations':[
        {'alias':'sample','source':'fixture','symbol':'A','raw_timestamp':when,'effective_time':when,
         'availability_time':when,'ingested_at':when,'source_revision':'1','payload':{'probe':1}}],
        'features':[{'feature_name':'probe','feature_version':'1','symbol':'A','asof_time':when,
                     'source_time':when,'availability_time':when,'value':1,'source_aliases':['sample']}]
    })+'\n',encoding='utf-8')
    root=Path(__file__).resolve().parents[1]
    command=[sys.executable,str(root/'tools/v33_research.py'),'replay','--journal',str(journal),
             '--run-id','cli-proof','--frames',str(frames),'--output',str(output)]
    first=subprocess.run(command,cwd=root,capture_output=True,text=True,encoding='utf-8',check=True)
    content=output.read_text(encoding='utf-8')
    second=subprocess.run(command,cwd=root,capture_output=True,text=True,encoding='utf-8',check=True)
    assert content==output.read_text(encoding='utf-8')
    assert json.loads(first.stdout)['binance_requests']==json.loads(second.stdout)['binance_requests']==0
    with sqlite3.connect(journal) as db:
        assert db.execute('SELECT COUNT(*) FROM csr_v33_evaluations').fetchone()[0]==1
        assert db.execute('SELECT revision FROM csr_runtime').fetchone()[0]==0
    rejected=subprocess.run(command[:-1]+[str(journal)],cwd=root,capture_output=True,text=True,encoding='utf-8')
    assert rejected.returncode==2
    with sqlite3.connect(journal) as db:assert db.execute('SELECT COUNT(*) FROM csr_v33_evaluations').fetchone()[0]==1
