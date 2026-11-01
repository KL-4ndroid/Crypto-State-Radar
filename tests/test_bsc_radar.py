from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import json
import pytest

from src.bsc.contracts import block_evidence,entity,TRANSFER,ZERO
from src.bsc.scanner import collect,ChainReorganization,REGISTRY_REFERENCE
from src.bsc.rpc import ReadRPC
from src.algorithms.bsc import BSCDiscovery
from src.algorithms.contracts import AlgorithmInput,StateMismatch
from src.record_stream import RecordStream,StreamCapacity
from src.research_journal import ResearchJournal,RevisionConflict
from src.request_budget import RequestBudget
from src.dashboard_view import publication,redis_publication

A='0x'+'a'*40
B='0x'+'b'*40
NOW=datetime(2026,11,1,tzinfo=timezone.utc)
def h(n):return '0x'+format(n,'064x')
def block(n=88):return {'number':hex(n),'hash':h(n),'parentHash':h(n-1),
    'timestamp':hex(int(NOW.timestamp())-100),'transactions':[h(n*100)]}
def receipts(n=88):return [{'transactionHash':h(n*100),'blockNumber':hex(n),'blockHash':h(n),
    'status':'0x1','contractAddress':A,'logs':[{'address':B,'transactionHash':h(n*100),
    'blockNumber':hex(n),'blockHash':h(n),'removed':False,'logIndex':'0x0',
    'topics':[TRANSFER,ZERO,'0x'+'0'*24+'c'*40]}]}]

class FakeRPC:
    def __init__(self):self.budget=RequestBudget(max_requests=100);self.broken=False;self.reorg=False
    def call(self,method,args):
        self.budget.begin()
        if method=='eth_chainId':return '0x38'
        if method=='eth_blockNumber':return '0x64'
        if method=='eth_getBlockByNumber':
            result=block(int(args[0],16))
            if self.reorg:result['hash']=h(999)
            return result
        if method=='eth_getBlockReceipts':return [] if self.broken else receipts(int(args[0],16))
        if method=='eth_getCode':return '0x6000'+REGISTRY_REFERENCE
        pytest.fail('unexpected RPC method')

@pytest.fixture
def stream(tmp_path):
    journal=ResearchJournal(tmp_path/'research.sqlite3')
    yield RecordStream(journal,'bsc:local:test')
    journal.close()

def test_compact_evidence_preserves_supported_discovery_and_identity():
    result=block_evidence(block(),receipts(),NOW)
    assert [r['kind'] for r in result['observations']]==['DIRECT_DEPLOYMENT','MINT_TRANSFER']
    assert entity(A)==f'eip155:56:{A}'
    assert result['receipt_count']==1

@pytest.mark.parametrize('change',[
    lambda rows:rows.clear(),
    lambda rows:rows.append(deepcopy(rows[0])),
    lambda rows:rows[0].update(blockHash=h(123)),
    lambda rows:rows[0]['logs'][0].update(removed=True),
    lambda rows:rows[0]['logs'].append(deepcopy(rows[0]['logs'][0])),
    lambda rows:rows[0].update(status='0x0'),
])
def test_incomplete_duplicate_or_reorganized_receipts_rejected(change):
    rows=receipts();change(rows)
    with pytest.raises(ValueError):block_evidence(block(),rows,NOW)

def test_collection_persists_cursor_evidence_and_versioned_project_without_futures_mutation(stream):
    before=stream.journal.load()
    result=collect(stream,FakeRPC())
    assert result['cursor']=={'number':88,'hash':h(88)}
    assert result['coverage_start_block']==88 and result['execution_enabled'] is False
    assert len(result['projects'])==2
    assert all(row['state']=='REVIEW_REQUIRED' for row in result['projects'].values())
    assert all('EXCLUSIVITY_NOT_PROVEN' in row['reasons'] for row in result['projects'].values())
    assert stream.read('block:'+h(88))['receipt_count']==1
    assert stream.journal.load()==before
    assert stream.journal.read('signals')==[]
    assert stream.journal.sql('SELECT COUNT(*) FROM csr_validation_jobs').fetchone()[0]==0
    old_count=stream.stats()['records']
    again=collect(stream,FakeRPC())
    assert again['cursor']==result['cursor'] and again['projects']==result['projects']
    assert stream.stats()['records']==old_count+2 # liveness receipt + head, no duplicate block

def test_partial_block_does_not_advance_cursor_or_fabricate_empty_results(stream):
    rpc=FakeRPC();rpc.broken=True
    with pytest.raises(ValueError):collect(stream,rpc)
    assert stream.load()[0]==0

def test_anchor_reorg_is_append_only_halts_and_preserves_cursor(stream):
    old=collect(stream,FakeRPC())
    rpc=FakeRPC();rpc.reorg=True
    with pytest.raises(ChainReorganization):collect(stream,rpc)
    _,saved=stream.load()
    assert saved['status']=='REORG_REVIEW' and saved['cursor']==old['cursor']
    rpc2=FakeRPC()
    with pytest.raises(ChainReorganization):collect(stream,rpc2)
    assert rpc2.budget.calls==0

def test_code_failure_keeps_pending_classification(stream):
    class NoCode(FakeRPC):
        def call(self,method,args):
            if method=='eth_getCode':raise RuntimeError('unavailable')
            return super().call(method,args)
    result=collect(stream,NoCode())
    assert all(not row['code_checked'] and row['state']=='DATA_PENDING' for row in result['projects'].values())

def test_stream_cas_immutability_and_capacity_are_atomic(stream):
    result=stream.commit(0,{'status':'first'},[('evidence:1',{'a':1})])
    with pytest.raises(RevisionConflict):stream.commit(0,{},[])
    with pytest.raises(ValueError):stream.commit(1,{},[('evidence:1',{'a':2})])
    assert stream.load()[1]==result
    limited=RecordStream(stream.journal,'bsc:local:limited',max_bytes=1000)
    rpc=FakeRPC()
    with pytest.raises(StreamCapacity):collect(limited,rpc)
    assert rpc.budget.calls==0

def test_public_projection_does_not_expose_private_or_strategy_state(stream):
    payload=collect(stream,FakeRPC())
    payload['rpc_secret']='do-not-publish'
    for row in payload['projects'].values():row['wallet_secret']='do-not-publish'
    public=publication(payload)
    assert public['meta']['snapshot_id']==public['view']['snapshot_id']
    assert 'do-not-publish' not in json.dumps(public)
    assert 'algorithm_state' not in json.dumps(public)
    assert 'pump_score' not in json.dumps(public)
    command=redis_publication('fixture',payload)
    assert command[0]=='EVAL' and command[2]==3

def test_rpc_disallows_wallet_or_broadcast_methods_before_any_network():
    rpc=ReadRPC(['https://fixture.invalid'])
    for name in ('eth_sendRawTransaction','eth_sendTransaction','personal_sign','eth_sign'):
        with pytest.raises(ValueError):rpc.call(name,[])
    assert rpc.budget.calls==0

def test_algorithm_reference_is_never_a_trade_or_safety_approval():
    algo=BSCDiscovery()
    result=algo.evaluate(AlgorithmInput(entity(A),NOW,values={'code_checked':True,'agent_id_reference':True},source_integrity=True))
    assert result.scores=={} and result.quality['execution_enabled'] is False
    assert 'EXCLUSIVITY_NOT_PROVEN' in result.reasons
    with pytest.raises(StateMismatch):algo.evaluate(AlgorithmInput(entity(B),NOW,previous_state=result.next_state))

def test_retired_futures_cannot_fetch_market_or_validate(monkeypatch):
    from src.run_cycle import run
    from src.validation_cycle import run_validation_cycle
    from src.scheduled_tasks import execute
    monkeypatch.setenv('CSR_PRODUCT_MODE','bsc')
    monkeypatch.setattr('src.run_cycle.collect_market',lambda *a,**k:pytest.fail('Futures called'))
    with pytest.raises(RuntimeError,match='retired'):run()
    with pytest.raises(RuntimeError,match='retired'):run_validation_cycle()
    monkeypatch.delenv('CSR_SCHEDULER_ENABLED',raising=False)
    assert execute('full_scan','fixture')['status']=='retired'

def test_bsc_render_has_no_legacy_manual_scan_or_score(monkeypatch):
    from api.dashboard import _render_dashboard
    monkeypatch.setenv('CSR_PRODUCT_MODE','bsc')
    html=_render_dashboard().decode()
    assert 'BSC Agent ID' in html and '未接交易錢包' in html
    assert '/api/scan' not in html and '/api/bsc' not in html


def test_saved_evidence_replays_identical_project_states_without_network(stream,monkeypatch):
    from src.bsc.replay import replay
    result=collect(stream,FakeRPC())
    rows=[json.loads(r[0]) for r in stream.journal.sql("SELECT payload FROM csr_records WHERE kind=? AND (record_key LIKE 'block:%' OR record_key LIKE 'code:%')",(stream.kind,)).fetchall()]
    monkeypatch.setattr('requests.post',lambda *a,**k:pytest.fail('replay called network'))
    a=replay(rows);b=replay(list(reversed(rows)))
    assert a==b and a['projects']==result['projects']
    assert a['blocks']==1 and a['execution_enabled'] is False


def test_code_evidence_cannot_precede_its_block(stream):
    from src.bsc.replay import replay
    with pytest.raises(ValueError):
        replay([{'kind':'CODE','observed_at':NOW.isoformat(),'entity_id':entity(A),
                 'block_number':88,'block_hash':h(88),'code':'0x6000'}])


def test_watchlist_range_coverage_and_replay_are_explicit(stream,monkeypatch):
    from src.bsc.log_scanner import collect_logs
    from src.bsc.replay import replay
    monkeypatch.setenv('CSR_BSC_WATCH_ADDRESSES',B)
    class Logs(FakeRPC):
        def call(self,method,args):
            if method=='eth_getLogs':
                assert args[0]['address']==[B]
                return receipts(88)[0]['logs']
            return super().call(method,args)
    output=collect_logs(stream,Logs(),start_block=87)
    assert output['coverage']=='WATCHLIST_MINT_RPC_RANGE'
    assert output['cursor']=={'number':88,'hash':h(88)}
    assert list(output['projects'])==[entity(B)]
    assert output['projects'][entity(B)]['first_seen_block']==88
    rows=[json.loads(r[0]) for r in stream.journal.sql('SELECT payload FROM csr_records WHERE kind=? AND (record_key LIKE ? OR record_key LIKE ?)',(stream.kind,'range:%','code:%')).fetchall()]
    assert replay(rows)['projects']==output['projects']
    monkeypatch.setenv('CSR_BSC_WATCH_ADDRESSES',A)
    rpc=Logs()
    with pytest.raises(ValueError,match='universe changed'):collect_logs(stream,rpc)
    assert rpc.budget.calls==0


def test_range_failure_never_becomes_empty_success_or_advanced_cursor(stream,monkeypatch):
    from src.bsc.log_scanner import collect_logs
    monkeypatch.setenv('CSR_BSC_WATCH_ADDRESSES',B)
    class Failed(FakeRPC):
        def call(self,method,args):
            if method=='eth_getLogs':raise RuntimeError('provider failure')
            return super().call(method,args)
    with pytest.raises(RuntimeError):collect_logs(stream,Failed())
    assert stream.load()[0]==0
