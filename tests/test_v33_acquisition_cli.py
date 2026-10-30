import json
from pathlib import Path
import subprocess
import sys


def test_plan_does_not_create_storage_or_request_network(tmp_path):
    root=Path(__file__).resolve().parents[1]
    journal=tmp_path/'not-created.db';archive=tmp_path/'not-created'
    command=[sys.executable,str(root/'tools/v33_acquire.py'),'plan','--run-id','test','--symbols','BTCUSDT',
             '--journal',str(journal),'--archive',str(archive)]
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',check=True)
    report=json.loads(result.stdout)
    assert report['namespace']=='SHADOW' and not report['production_writes'] and not report['scheduled']
    assert not journal.exists() and not archive.exists()
    invalid=subprocess.run(command+['--output',str(journal)],capture_output=True,text=True,encoding='utf-8')
    assert invalid.returncode==2 and not journal.exists()
