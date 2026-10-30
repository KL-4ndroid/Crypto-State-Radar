from datetime import datetime, timedelta, timezone
import json

import pytest
from src import demand_scheduler as demand, scheduled_tasks
from src.research_journal import ResearchJournal

NOW = datetime(2026,10,30,14,0,tzinfo=timezone.utc)


@pytest.fixture
def journal(tmp_path):
    store=ResearchJournal(tmp_path/'ledger.sqlite3')
    yield store
    store.close()


def test_empty_work_schedules_nothing_and_does_not_advance_model(journal):
    assert demand.reconcile(journal,NOW,lambda *args:pytest.fail('no wake needed'))==0
    assert journal.load()[0]==0


def test_pending_intent_survives_publish_failure_and_retries_same_token(journal):
    journal.commit_cycle(0,{'pending':{'A':{}}},[])
    calls=[]
    def fail(*args):
        calls.append(args)
        raise TimeoutError('unknown provider outcome')
    with pytest.raises(TimeoutError):demand.reconcile(journal,NOW,fail)
    assert demand.reconcile(journal,NOW,lambda *args:calls.append(args))==1
    assert calls[0]==calls[1]
    assert calls[0][2]==NOW+timedelta(seconds=300)
    assert demand.reconcile(journal,NOW+timedelta(seconds=60),lambda *args:pytest.fail('already sent'))==0
    assert journal.load()[0]==1


def test_expired_delivery_and_killed_lease_recover_without_accepting_stale_token(journal):
    journal.commit_cycle(0,{'pending':{'A':{}}},[])
    operation,token,due=demand.plan(journal,NOW)[0]
    with pytest.raises(RuntimeError):demand.claim(journal,operation,token,NOW)
    assert demand.claim(journal,operation,token,due)
    with pytest.raises(RuntimeError):demand.claim(journal,operation,token,due)
    assert demand.plan(journal,due+timedelta(seconds=60))==[]
    replacement=demand.plan(journal,due+timedelta(seconds=121))[0]
    assert replacement[1]!=token
    assert not demand.claim(journal,operation,token,replacement[2])
    assert demand.claim(journal,*replacement[:2],replacement[2])
    demand.release(journal,*replacement[:2])
    assert demand.claim(journal,*replacement[:2],replacement[2])
    demand.finish(journal,*replacement[:2])
    assert not demand.claim(journal,*replacement[:2],replacement[2])


def test_one_validation_wake_for_all_horizons_honors_retry_and_processing_lease(journal):
    with journal.transaction():
        for i in range(50):
            journal.sql("INSERT INTO csr_validation_jobs(signal_id,horizon,due_at,next_attempt_at,status) VALUES(?,?,?,?,?)",
                        (str(i),'1h',(NOW+timedelta(hours=1)).isoformat(),(NOW+timedelta(hours=2)).isoformat(),'pending'))
    planned=demand.plan(journal,NOW)
    assert len(planned)==1 and planned[0][0]=='validate' and planned[0][2]==NOW+timedelta(hours=2)
    with journal.transaction():
        journal.sql("UPDATE csr_validation_jobs SET status='processing',lease_until=? WHERE signal_id='0'",((NOW+timedelta(minutes=2)).isoformat(),))
    earlier=demand.plan(journal,NOW)[0]
    assert earlier[1]!=planned[0][1] and earlier[2]==NOW+timedelta(minutes=2)
    assert not demand.claim(journal,'validate',planned[0][1],NOW+timedelta(hours=2))


def test_active_lifecycle_and_failed_backlog_keep_future_wake(journal):
    journal.commit_cycle(0,{'states':{'A':{'state':'LONG_READY','updated_at':NOW.isoformat()}},
                          'pending':{'B':{'next_attempt_at':(NOW+timedelta(hours=2)).isoformat()}}},[])
    planned=demand.plan(journal,NOW)
    assert planned[0][0]=='drain' and planned[0][2]==NOW+timedelta(hours=1)
    journal.commit_cycle(1,{'states':{},'pending':{}},[])
    assert demand.plan(journal,NOW)==[]
    assert not demand.claim(journal,*planned[0][:2],planned[0][2])


def test_provider_timeout_redaction_and_shared_flow_key(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('VERCEL_ENV','preview')
    monkeypatch.setenv('CSR_RESEARCH_ENVIRONMENT','preview')
    monkeypatch.setenv('CSR_SCHEDULER_ORIGIN','https://isolated.example')
    monkeypatch.setenv('QSTASH_URL','https://qstash.upstash.io')
    monkeypatch.setenv('QSTASH_TOKEN','private-token')
    monkeypatch.setenv('CSR_SCHEDULER_PREVIEW_AUTH','private-preview')
    calls=[]
    monkeypatch.setattr('requests.post',lambda *args,**kwargs:calls.append((args,kwargs)) or SimpleNamespace(ok=True))
    demand.publish('validate','a'*32,NOW)
    args,kwargs=calls[0]
    assert kwargs['timeout']==(2,3)
    assert kwargs['headers']['Upstash-Flow-Control-Key']=='csr-preview-v1'
    assert kwargs['headers']['Upstash-Forward-x-vercel-trusted-oidc-idp-token']=='private-preview'
    assert kwargs['json']=={'operation':'validate','wake_token':'a'*32}
    monkeypatch.setenv('VERCEL_ENV','production')
    with pytest.raises(RuntimeError):demand.publish('validate','a'*32,NOW)
    assert len(calls)==1


def test_repair_publishes_recovery_and_plans_without_fetching_market(monkeypatch,journal):
    monkeypatch.setenv('CSR_SCHEDULER_ENABLED','true')
    monkeypatch.setenv('CSR_SCHEDULER_MODE','demand')
    monkeypatch.setenv('VERCEL_ENV','preview')
    monkeypatch.setenv('CSR_RESEARCH_ENVIRONMENT','preview')
    monkeypatch.setenv('CSR_RESEARCH_DATABASE_URL','not-read-by-test')
    journal.commit_cycle(0,{'last_scan':{'mode':'live'},'pending':{}},[])
    monkeypatch.setattr(scheduled_tasks,'open_journal',lambda root:journal)
    monkeypatch.setattr(journal,'close',lambda:None)
    monkeypatch.setattr(scheduled_tasks,'persist_receipt',lambda row:None)
    monkeypatch.setattr(scheduled_tasks,'run',lambda **kw:pytest.fail('must not fetch market'))
    calls=[]
    monkeypatch.setattr(scheduled_tasks,'work',lambda operation,**kw:calls.append(operation) or {'published':True})
    result=scheduled_tasks.execute('repair','repair-test',True)
    assert calls==['publish'] and result['wakes_published']==0
    assert result['request_metrics']['requests']==0 and result['success']


def test_completed_work_retry_repairs_failed_next_wake_without_second_scan(monkeypatch,journal):
    for key,value in {'CSR_SCHEDULER_ENABLED':'true','CSR_SCHEDULER_MODE':'demand',
        'VERCEL_ENV':'preview','CSR_RESEARCH_ENVIRONMENT':'preview','CSR_RESEARCH_DATABASE_URL':'fixture'}.items():
        monkeypatch.setenv(key,value)
    journal.commit_cycle(0,{'pending':{'A':{}}},[])
    operation,token,due=demand.plan(journal,NOW)[0]
    # The signed delivery is now due, using a real durable claim.
    with journal.transaction():
        journal.sql('UPDATE csr_scheduler_wakes SET due_at=?',((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    monkeypatch.setattr(scheduled_tasks,'open_journal',lambda root:journal)
    monkeypatch.setattr(journal,'close',lambda:None)
    monkeypatch.setattr(scheduled_tasks,'persist_receipt',lambda row:None)
    calls=[]
    monkeypatch.setattr(scheduled_tasks,'work',lambda operation,**kw:calls.append(operation) or {'status':'ok'})
    def fail(*args):raise TimeoutError()
    monkeypatch.setattr(demand,'publish',fail)
    with pytest.raises(RuntimeError):scheduled_tasks.execute('drain','delivery',wake_token=token)
    # finish persisted before the network failure. Retry must reconcile intent,
    # even though the original token has been superseded/completed.
    published=[]
    monkeypatch.setattr(demand,'publish',lambda *args:published.append(args))
    result=scheduled_tasks.execute('drain','retry-delivery',wake_token=token)
    assert calls==['drain'] and len(published)==1 and result['status']=='superseded'
