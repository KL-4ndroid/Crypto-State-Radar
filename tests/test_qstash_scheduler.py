import base64
import hashlib
import io
import json
import time

import jwt
import pytest

from api import scheduler
from src import scheduled_tasks,run_cycle
from src.research_journal import ResearchJournal

CURRENT = 'test-current-key-01234567890123456789'
NEXT = 'test-next-key-01234567890123456789012'
URL = 'https://isolated.example/api/scheduler'


def signed(body, key=CURRENT, **claims):
    data = dict(iss='Upstash',sub=URL,nbf=int(time.time())-1,exp=int(time.time())+60,
        body=base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip('='))
    return jwt.encode(dict(data,**claims),key,algorithm='HS256')


def request(body, signature):
    h = scheduler.handler.__new__(scheduler.handler)
    h.path='/api/scheduler'
    h.headers={'Content-Length':str(len(body)),'Upstash-Signature':signature,'Upstash-Message-Id':'msg_test'}
    h.rfile=io.BytesIO(body)
    output=[]
    h._send=lambda status,payload:output.append((status,payload))
    h.do_POST()
    return output[0]


def test_signature_blocks_tampering_expiry_and_other_deployment(monkeypatch):
    monkeypatch.setenv('QSTASH_CURRENT_SIGNING_KEY',CURRENT)
    monkeypatch.setenv('QSTASH_NEXT_SIGNING_KEY',NEXT)
    monkeypatch.setenv('CSR_SCHEDULER_ORIGIN','https://isolated.example')
    calls=[]
    monkeypatch.setattr(scheduler,'execute',lambda *args:calls.append(args) or {'success':True})
    body=b'{"operation":"drain"}'
    for signature in ('',signed(body,sub='https://production.example/api/scheduler'),signed(body,exp=int(time.time())-60)):
        assert request(body,signature)[0]==401
    assert request(b'{"operation":"full_scan"}',signed(body))[0]==401
    assert calls==[]
    assert request(body,signed(body))[0]==200
    assert request(body,signed(body,key=NEXT))[0]==200
    assert len(calls)==2


def test_disabled_and_environment_mismatch_never_start_work(monkeypatch):
    monkeypatch.setattr(scheduled_tasks,'run',lambda **kw:pytest.fail('must not scan'))
    monkeypatch.setenv('CSR_SCHEDULER_ENABLED','false')
    assert scheduled_tasks.execute('full_scan','msg_test')['status']=='disabled'
    monkeypatch.setenv('CSR_SCHEDULER_ENABLED','true')
    monkeypatch.setenv('VERCEL_ENV','preview')
    monkeypatch.setenv('CSR_RESEARCH_ENVIRONMENT','production')
    with pytest.raises(RuntimeError):scheduled_tasks.execute('full_scan','msg_test')


def test_duplicate_delivery_skips_collection_and_revision(monkeypatch,tmp_path):
    monkeypatch.setenv('CSR_STATE_FILE',str(tmp_path/'state.json'))
    for key in ('VERCEL','VERCEL_ENV','CSR_RESEARCH_DATABASE_URL','KV_REST_API_URL','KV_REST_API_TOKEN','CSR_STATE_STORE_URL'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('CSR_SCAN_MODE','live')
    calls=[]
    monkeypatch.setattr(run_cycle,'collect_market',lambda symbols=None:calls.append(1) or [{'symbol':'A','price':1,'open_interest':1000}])
    monkeypatch.setattr(run_cycle,'derive_live_features',lambda symbol:{'price_change_1h':0,'price_change_4h':0})
    first=run_cycle.run(root=str(tmp_path),operation_id='qstash:msg_test:full_scan')
    replay=run_cycle.run(root=str(tmp_path),operation_id='qstash:msg_test:full_scan')
    store=ResearchJournal(tmp_path/'research.sqlite3')
    revision,_=store.load()
    assert revision==first['revision']==replay['revision']
    assert replay['deduplicated'] and len(calls)==1
    assert len(store.read('runs'))==1
    store.close()


def test_replay_recovers_publication_and_redacts_payload(monkeypatch,capsys):
    monkeypatch.setenv('CSR_SCHEDULER_ENABLED','true')
    monkeypatch.setenv('VERCEL_ENV','preview')
    monkeypatch.setenv('CSR_RESEARCH_ENVIRONMENT','preview')
    monkeypatch.setenv('CSR_RESEARCH_DATABASE_URL','private-dsn')
    monkeypatch.setattr(scheduled_tasks,'run',lambda **kw:{'mode':'live','revision':2,'deduplicated':True,'notifications':['secret-market-payload']})
    calls=[]
    monkeypatch.setattr(scheduled_tasks,'work',lambda operation,**kw:calls.append(operation) or {'published':True})
    result=scheduled_tasks.execute('full_scan','msg_test',True)
    assert calls==['publish'] and result['snapshot_published']
    assert result['trigger']=='qstash_schedule'
    assert 'private-dsn' not in json.dumps(result)+capsys.readouterr().out
    assert 'secret-market-payload' not in json.dumps(result)
