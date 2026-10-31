"""Opt-in tests against disposable, loopback-only PostgreSQL and Redis."""
import json
import os
import socket
import threading
from pathlib import Path
from uuid import uuid4
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from src.research_journal import ResearchJournal,RevisionConflict
from src.state_store import UpstashRestStateStore

pytestmark=pytest.mark.skipif(os.getenv("CSR_LOCAL_STORAGE_ACCEPTANCE")!="1",reason="run tools/storage_acceptance.py for isolated services")


def test_v33_journal_archive_global_quota_restore_and_runtime_isolation(postgres):
    from src.v33.journal_archive import JournalArchive
    open_store,admin,psycopg=postgres
    a,b=open_store(),open_store()
    original=a.load()
    def upload(pair):
        journal,body=pair
        try:
            return JournalArchive(journal,max_objects=1).put(body)
        except OSError:
            return None
    with admin.transaction():
        admin.execute('SELECT id FROM csr_runtime WHERE id=1 FOR UPDATE')
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows=list(pool.map(upload,[(a,b'first'),(b,b'second')]))
    assert sum(row is not None for row in rows)==1
    index=next(i for i,row in enumerate(rows) if row is not None)
    blob=rows[index]
    restored=JournalArchive(open_store()).read(blob['sha256'])
    assert restored==(b'first' if index==0 else b'second')
    assert JournalArchive(a).stats()['objects']==1
    assert a.load()==original
    assert JournalArchive(a,max_objects=1).put(restored)==blob
    with pytest.raises(psycopg.Error):
        admin.execute("DELETE FROM csr_records WHERE kind='v33:raw:archive:gzip-1'")


@pytest.fixture
def postgres():
    psycopg=pytest.importorskip("psycopg")
    from psycopg import sql
    port=int(os.environ["CSR_TEST_PG_PORT"])
    dsn=f"host=127.0.0.1 port={port} dbname=radar_acceptance user=postgres password=local-acceptance-only connect_timeout=5"
    admin=psycopg.connect(dsn,autocommit=True)
    schema="csr_test_"+uuid4().hex
    role=schema+"_role"
    connections=[]
    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        migration=(Path(__file__).resolve().parents[1]/"schema/runtime_ledger.sql").read_text(encoding="utf-8")
        for _ in range(2):
            with admin.transaction():admin.execute(migration)
        admin.execute("UPDATE csr_runtime SET environment='test' WHERE id=1")
        admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema),sql.Identifier(role)))
        for privileges,table in (("SELECT,INSERT","csr_records"),("SELECT","csr_runtime"),("SELECT,INSERT,UPDATE","csr_validation_jobs")):
            admin.execute(sql.SQL("GRANT "+privileges+" ON {} TO {}").format(sql.Identifier(table),sql.Identifier(role)))
        admin.execute(sql.SQL("GRANT UPDATE(revision,payload) ON csr_runtime TO {}").format(sql.Identifier(role)))
        def open_store():
            connection=psycopg.connect(dsn,autocommit=True,options=f"-c search_path={schema}")
            connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            connections.append(connection)
            return ResearchJournal(connection=connection,initialize=False,expected_environment="test")
        yield open_store,admin,psycopg
    finally:
        for connection in connections:connection.close()
        admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        admin.close()


def test_postgres_migration_roles_rollback_and_concurrent_commits(postgres):
    open_store,admin,psycopg=postgres
    first,second=open_store(),open_store()
    # Pooler-compatible timeouts apply to every query/transaction and do not
    # remain on a server connection when the transaction is returned to a pool.
    baseline=first.db.execute("SHOW statement_timeout").fetchone()[0]
    assert first.sql("SHOW statement_timeout").fetchone()[0]=='5s'
    assert first.db.execute("SHOW statement_timeout").fetchone()[0]==baseline
    with first.transaction():
        assert first.sql("SHOW statement_timeout").fetchone()[0]=='5s'
        assert first.sql("SHOW lock_timeout").fetchone()[0]=='2s'
    signal={"signal_id":"s","observed_at":"2026-01-01T08:00:00+08:00","entry_price":100}
    first.commit_cycle(0,{"states":{}},[("signals","s",signal)])
    assert first.load()[1]["ledger_id"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        first.sql("UPDATE csr_records SET payload='{}'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        first.sql("CREATE TABLE forbidden (id INTEGER)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        first.sql("UPDATE csr_runtime SET environment='production'")
    with pytest.raises(psycopg.Error,match="immutable"):
        admin.execute("UPDATE csr_records SET payload='{}'")
    with pytest.raises(ValueError):
        first.commit_cycle(1,{"states":{}},[("observations","rolled-back",{}),("signals","s",dict(signal,entry_price=999))])
    assert first.read("observations")==[] and first.load()[0]==1
    barrier=threading.Barrier(2)
    def commit(store):
        rev,_=store.load();barrier.wait(timeout=5)
        try:store.commit_cycle(rev,{"states":{}},[]);return "committed"
        except RevisionConflict:return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(commit,[first,second]))==["committed","conflict"]
    assert first.load()[0]==2
    wrong_environment=open_store()
    with pytest.raises(RuntimeError,match="environment mismatch"):
        ResearchJournal(connection=wrong_environment.db,initialize=False,expected_environment="preview")


def test_postgres_leases_skip_locked_retry_and_stale_completion(postgres):
    open_store,_,_=postgres
    a,b=open_store(),open_store()
    now=datetime(2026,1,10,tzinfo=timezone.utc)
    a.commit_cycle(0,{"states":{}},[("signals","s",{"signal_id":"s","observed_at":"2026-01-01T00:00:00+00:00","entry_price":100})])
    claimed=a.claim_due_jobs(now,limit=1,lease_now=now)
    sid,horizon,old_token=claimed[0]
    others=b.claim_due_jobs(now,limit=20,lease_now=now)
    assert len(others)==6 and (sid,horizon) not in {(s,h) for s,h,_ in others}
    recovered=b.claim_due_jobs(now+timedelta(minutes=3),limit=1,lease_now=now+timedelta(minutes=3))
    assert recovered and recovered[0][:2]==(sid,horizon)
    assert a.finish_validation(sid,horizon,row={"bad":True},lease_token=old_token,now=now+timedelta(minutes=3)) is False
    new_token=recovered[0][2]
    assert b.finish_validation(sid,horizon,error="upstream",lease_token=new_token,now=now+timedelta(minutes=3))
    immediate=a.claim_due_jobs(now+timedelta(minutes=3),limit=20,lease_now=now+timedelta(minutes=3))
    assert (sid,horizon) not in {(s,h) for s,h,_ in immediate}
    assert not a.read("validations")


def redis_command(*parts):
    with socket.create_connection(("127.0.0.1",int(os.environ["CSR_TEST_REDIS_PORT"])),timeout=5) as sock:
        encoded=[str(p).encode() for p in parts]
        sock.sendall(b"*"+str(len(encoded)).encode()+b"\r\n"+b"".join(b"$"+str(len(p)).encode()+b"\r\n"+p+b"\r\n" for p in encoded))
        stream=sock.makefile("rb")
        def read():
            kind=stream.read(1);value=stream.readline()[:-2]
            if kind==b"+":return value.decode()
            if kind==b"-":raise RuntimeError(value.decode())
            if kind==b":":return int(value)
            if kind==b"$":
                size=int(value)
                if size==-1:return None
                result=stream.read(size);stream.read(2);return result.decode()
            raise RuntimeError("unsupported Redis response")
        return read()


def test_postgres_outbox_and_real_redis_atomic_publication(postgres,monkeypatch):
    open_store,_,_=postgres
    journal=open_store()
    key="csr:acceptance:"+uuid4().hex
    class RestBridge(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            assert self.headers["Authorization"]=="Bearer local-acceptance"
            command=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:result={"result":redis_command(*command)}
            except RuntimeError as exc:result={"error":str(exc)}
            body=json.dumps(result).encode();self.send_response(200);self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(("127.0.0.1",0),RestBridge)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        store=UpstashRestStateStore(f"http://127.0.0.1:{server.server_port}","local-acceptance",key=key,load=False)
        one=journal.commit_cycle(0,{"schema_version":1,"states":{}},[])
        two=journal.commit_cycle(1,{"schema_version":1,"states":{}},[])
        barrier=threading.Barrier(2)
        def publish(payload):barrier.wait(timeout=5);return store.publish_snapshot(payload)
        with ThreadPoolExecutor(max_workers=2) as executor:list(executor.map(publish,[two,one]))
        assert json.loads(redis_command("GET",key))["revision"]==2
        public_before=redis_command("GET",key+":dashboard-meta")
        view_before=json.loads(redis_command("GET",key+":dashboard-latest"))
        assert json.loads(public_before)["snapshot_id"]==view_before["snapshot_id"]
        with pytest.raises(RuntimeError):store.publish_snapshot(dict(two,revision=99,ledger_id="foreign"))
        assert json.loads(redis_command("GET",key))["ledger_id"]==two["ledger_id"]
        assert not store.publish_snapshot(one)
        assert redis_command("GET",key+":dashboard-meta")==public_before
        from src import run_cycle
        monkeypatch.setenv("CSR_SCAN_MODE","live")
        monkeypatch.delenv("VERCEL_ENV",raising=False)
        monkeypatch.setattr(run_cycle,"open_journal",lambda root:open_store())
        monkeypatch.setattr(run_cycle,"state_store_from_env",lambda load=True:store)
        monkeypatch.setattr(run_cycle,"collect_market",lambda symbols=None:[{"symbol":"A","observed_at":datetime.now(timezone.utc).isoformat(),
            "price":100,"open_interest":100,"oi_change_1h":0.05,"oi_change_4h":0.1,"oi_change_24h":0.24}])
        monkeypatch.setattr(run_cycle,"derive_live_features",lambda symbol:{"price_change_1h":5,"price_change_4h":3,"volume_z":4,"volatility_compression":0.6,"funding_z":-2})
        summary=run_cycle.run()
        assert summary["snapshot_published"] and summary["revision"]==3 and summary["signals"]==1
        published=json.loads(redis_command("GET",key))
        assert published["states"]["A"]["state"]=="LONG_READY"
        meta=json.loads(redis_command("GET",key+":dashboard-meta"))
        view=json.loads(redis_command("GET",key+":dashboard-latest"))
        assert meta["snapshot_id"]==view["snapshot_id"] and view["states"]["A"]["state"]=="LONG_READY"
        assert "ledger_id" not in view
        assert len(journal.read("signals"))==1
        # The Dashboard contract also works with the schema-1 writer, without
        # migrating or enabling the new research runtime on a legacy deployment.
        from src.state_machine import SymbolState
        legacy=UpstashRestStateStore(store.base_url,store.token,key=key+":legacy",load=False)
        legacy.put_all({"OLD":SymbolState(symbol="OLD",state="WATCH",last_price=100)})
        legacy_meta=json.loads(redis_command("GET",key+":legacy:dashboard-meta"))
        legacy_view=json.loads(redis_command("GET",key+":legacy:dashboard-latest"))
        assert legacy_meta["snapshot_id"]==legacy_view["snapshot_id"]
        assert legacy_view["states"]["OLD"]["last_price"]==100
        assert legacy_meta["scan_status"]=="unknown"
    finally:
        redis_command("DEL",key,key+":dashboard-meta",key+":dashboard-latest",key+":legacy",key+":legacy:dashboard-meta",key+":legacy:dashboard-latest")
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_v33_postgres_namespace_concurrency_and_immutable_results(postgres):
    from src.v33.journal import ContractStore
    from src.v33.registry import defaults
    from src.v33.contracts import RawObservation,Feature,stamp
    open_store,admin,psycopg=postgres
    from psycopg import sql
    migration=(Path(__file__).resolve().parents[1]/'schema/v33_contract.sql').read_text(encoding='utf-8')
    for _ in range(2):
        with admin.transaction():admin.execute(migration)
    # The fixture runtime role is explicit; retain its restricted privileges.
    a,b=open_store(),open_store()
    runtime_role=a.db.execute('SELECT current_user').fetchone()[0]
    for table in ('csr_v33_features','csr_v33_evaluations','csr_v33_events','csr_v33_heads'):
        admin.execute(sql.SQL('GRANT SELECT,INSERT ON {} TO {}').format(sql.Identifier(table),sql.Identifier(runtime_role)))
    admin.execute(sql.SQL('GRANT UPDATE(revision,last_time,payload) ON csr_v33_heads TO {}').format(sql.Identifier(runtime_role)))
    stores=[ContractStore(a,'REPLAY','postgres'),ContractStore(b,'REPLAY','postgres')]
    policy=defaults();stores[0].put_rule(policy)
    now=datetime(2026,10,30,tzinfo=timezone.utc)
    raw=stores[0].put_raw(RawObservation('fixture','A',stamp(now),stamp(now),stamp(now),stamp(now),'1',{}))
    feature=stores[0].put_feature(Feature('probe','1','A',stamp(now),stamp(now),stamp(now),1,(raw,)))
    def evaluate(store):return store.evaluate('A',now,policy['rule_version'],[feature])
    with ThreadPoolExecutor(max_workers=2) as executor:
        first,second=list(executor.map(evaluate,stores))
    assert first==second and first['revision']==1
    assert admin.execute('SELECT COUNT(*) FROM csr_v33_evaluations').fetchone()[0]==1
    assert a.load()[0]==0
    for minutes in (5,10):
        when=now+timedelta(minutes=minutes)
        values={'return_7d':100,'quote_volume_24h':1000,'quote_volume_median_30d':100,'bar_5m':stamp(when)}
        raw=stores[0].put_raw(RawObservation('fixture','A',stamp(when),stamp(when),stamp(when),stamp(when),'1',values))
        ids=[stores[0].put_feature(Feature(name,'1','A',stamp(when),stamp(when),stamp(when),value,(raw,))) for name,value in values.items()]
        last=stores[0].evaluate('A',when,policy['rule_version'],ids)
    assert last['phase']=='PUMP' and last['events'][0]['event_type']=='PHASE_PUMP'
    for table in ('csr_v33_features','csr_v33_evaluations','csr_v33_events'):
        with pytest.raises(psycopg.Error):
            with admin.transaction():admin.execute(sql.SQL('UPDATE {} SET payload=%s').format(sql.Identifier(table)),('{}',))
    other=ContractStore(a,'BACKTEST','different')
    with pytest.raises(ValueError):other.evaluate('A',now,policy['rule_version'],[feature])


def test_v33_acquisition_postgres_atomic_cursor_and_leases(postgres,tmp_path,monkeypatch):
    from src.v33.journal import ContractStore
    from src.v33.acquisition import AcquisitionQueue,StaleAcquisition
    from src.v33.archive import FileArchive
    from src.v33.contracts import stamp
    from psycopg import sql
    open_store,admin,psycopg=postgres
    root=Path(__file__).resolve().parents[1]
    with admin.transaction():admin.execute((root/'schema/v33_contract.sql').read_text(encoding='utf-8'))
    for _ in range(2):
        with admin.transaction():admin.execute((root/'schema/v33_acquisition.sql').read_text(encoding='utf-8'))
    a,b=open_store(),open_store()
    role=a.db.execute('SELECT current_user').fetchone()[0]
    for table in ('csr_v33_acquisition','csr_v33_acquisition_items'):
        admin.execute(sql.SQL('GRANT SELECT,INSERT ON {} TO {}').format(sql.Identifier(table),sql.Identifier(role)))
    admin.execute(sql.SQL('GRANT UPDATE(revision,cursor_payload,next_due,attempts,lease_token,lease_until,last_error) ON csr_v33_acquisition TO {}').format(sql.Identifier(role)))
    stores=[ContractStore(j,'SHADOW','acquisition-test') for j in (a,b)]
    queues=[AcquisitionQueue(s) for s in stores]
    now=datetime(2026,10,31,tzinfo=timezone.utc);start=int(now.timestamp()*1000)
    queues[0].ensure('oi','BTCUSDT',start,now)
    def claim(queue):return queue.claim('oi','BTCUSDT',now)
    with admin.transaction():
        admin.execute('SELECT id FROM csr_runtime WHERE id=1 FOR UPDATE')
        with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(claim,queues))
        assert sum(c is not None for c in claims)==1  # No dependency on live runtime lock.
    winner=next(i for i,c in enumerate(claims) if c is not None);queue=queues[winner];store=stores[winner];claim=claims[winner]
    archive=FileArchive(tmp_path/'raw');blob=archive.put(b'[{"quantity":"1"}]')
    item={'key':str(start),'effective_time':stamp(now),'values':{'raw_timestamp':start,'quantity':'1','notional':'10'}}
    original=queue.journal._insert
    def fail_manifest(kind,key,row):
        if kind==store.kind('acquisitions'):raise RuntimeError('injected manifest failure')
        return original(kind,key,row)
    monkeypatch.setattr(queue.journal,'_insert',fail_manifest)
    with pytest.raises(RuntimeError):queue.finish(claim,[item],claim['cursor'],{'archive':blob},now,now,completed_at=now)
    assert queue.status('oi','BTCUSDT')['revision']==0
    assert admin.execute('SELECT COUNT(*) FROM csr_v33_acquisition_items').fetchone()[0]==0
    assert store.journal.read(store.kind('raw'))==[]
    monkeypatch.setattr(queue.journal,'_insert',original)
    receipt=queue.finish(claim,[item],claim['cursor'],{'archive':blob},now,now,completed_at=now)
    assert receipt['new_records']==1 and queue.status('oi','BTCUSDT')['revision']==1
    with pytest.raises(StaleAcquisition):queue.finish(claim,[item],claim['cursor'],{'archive':blob},now,now,completed_at=now)
    with pytest.raises(psycopg.Error):
        with admin.transaction():admin.execute("UPDATE csr_v33_acquisition_items SET content_hash='changed'")
    assert a.load()[0]==0 and archive.read(blob['sha256'])==b'[{"quantity":"1"}]'
