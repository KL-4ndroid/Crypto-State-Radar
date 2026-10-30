import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from src.operation_log import emit_operation
from src.research_journal import ResearchJournal
from src import run_cycle,research_worker


def test_operation_receipt_redacts_credentials_and_payloads(capsys):
    secret='do-not-log-this-secret'
    handler=SimpleNamespace(headers={'Authorization':'Bearer '+secret,'Cookie':secret,
        'X-Radar-Trigger':'schedule','X-Radar-Run-Id':'123'})
    emit_operation(handler,'validate',200,result={'validated':[{'private':secret}],'errors':[],
        'notifications':[secret],'revision':4},error=RuntimeError(secret))
    output=capsys.readouterr().out;assert secret not in output
    row=json.loads(output)
    assert row['trigger']=='schedule' and row['workflow_run_id']=='123'
    assert row['validated_count']==1 and row['errors_count']==0 and row['error_type']=='RuntimeError'


def test_continuation_preserves_last_full_scan(monkeypatch,tmp_path):
    monkeypatch.setenv('CSR_STATE_FILE',str(tmp_path/'state.json'))
    for key in ('VERCEL','VERCEL_ENV','CSR_RESEARCH_DATABASE_URL','KV_REST_API_URL','KV_REST_API_TOKEN','CSR_STATE_STORE_URL'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('CSR_SCAN_MODE','live')
    stamp=datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(run_cycle,'collect_market',lambda symbols=None:[{'symbol':'A','price':1,
        'open_interest':1000,'oi_change_1h':0,'oi_change_4h':0,'oi_change_24h':0,'observed_at':stamp}])
    monkeypatch.setattr(run_cycle,'derive_live_features',lambda symbol:{'price_change_1h':0,'price_change_4h':0})
    full=run_cycle.run(root=str(tmp_path))
    journal=ResearchJournal(tmp_path/'research.sqlite3');revision,payload=journal.load()
    payload['pending']={'A':{'due_since':stamp,'reason':'rotation'}}
    journal.commit_cycle(revision,payload,[]);journal.close()
    deep=run_cycle.run(root=str(tmp_path),drain=True)
    journal=ResearchJournal(tmp_path/'research.sqlite3');_,payload=journal.load();journal.close()
    assert payload['last_scan']['run_id']==deep['run_id']
    assert payload['last_full_scan']['run_id']==full['run_id']
    assert payload['last_full_scan']['processed_at']==full['processed_at']


def test_worker_freshness_does_not_hide_overdue_or_unknown_full_scan(monkeypatch):
    now=datetime.now(timezone.utc)
    payload={'last_scan':{'mode':'live','drain':True,'processed_at':now.isoformat()},'states':{}}
    monkeypatch.setattr(research_worker,'open_journal',lambda root:SimpleNamespace(load=lambda:(1,payload),close=lambda:None))
    assert research_worker.work('status')['full_scan_status']=='unknown'
    payload['last_full_scan']={'processed_at':(now-timedelta(hours=2)).isoformat(),'scanned':522}
    assert research_worker.work('status')['full_scan_status']=='overdue'
    payload['last_full_scan']={'processed_at':now.isoformat(),'scanned':0,'errors':522}
    assert research_worker.work('status')['full_scan_status']=='failed'
