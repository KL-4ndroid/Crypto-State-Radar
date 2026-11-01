from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json

import pytest
from hypothesis import given,settings,strategies as st
from src.research_journal import ResearchJournal
from src.v33.journal import ContractStore,install_local
from src.v33.archive import FileArchive
from src.v33.acquisition import AcquisitionQueue,install_acquisition,StaleAcquisition
from src.v33.acquisition_sources import (request_plan,normalize_statistics,normalize_trades,
    ProviderError,AuthRequired,DataGap,BinanceAcquisitionHTTP)
from src.v33.acquisition_worker import AcquisitionWorker
from src.v33.acquisition_features import derive_measurements,FEATURE_VERSION
from src.v33.contracts import stamp
from src.request_budget import RequestBudget

BASE=datetime(2026,10,30,tzinfo=timezone.utc)
MS=int(BASE.timestamp()*1000)
NOW=BASE+timedelta(hours=1,minutes=1)


class Clock:
    value=NOW
    def __call__(self):return self.value


@pytest.fixture
def env(tmp_path):
    journal=ResearchJournal(tmp_path/'capture.db');install_local(journal);install_acquisition(journal);install_acquisition(journal)
    store=ContractStore(journal,'SHADOW','pilot')
    yield store,FileArchive(tmp_path/'raw'),Clock()
    journal.close()


class FakeHTTP:
    def __init__(self,clock,callback):self.clock=clock;self.callback=callback;self.calls=[];self.budget=None
    def fetch(self,source,params):
        if self.budget:self.budget.begin()
        self.calls.append((source,params))
        try:
            payload=self.callback(source,params)
            return payload,json.dumps(payload).encode(),self.clock()
        finally:
            if self.budget:self.budget.finish(0,False)


def worker(env,callback,max_requests=12,page_limit=500):
    store,archive,clock=env;http=FakeHTTP(clock,callback)
    result=AcquisitionWorker(store,archive,http,max_requests=max_requests,page_limit=page_limit,clock=clock)
    http.budget=result.budget
    return result,http


def statistics(source,params):
    rows=[]
    for when in range(params['startTime'],params['endTime']+1,300000):
        if len(rows)==params['limit']:break
        base={'symbol':params['symbol'],'timestamp':when}
        n=(when-MS)//300000
        if source=='oi':base.update(sumOpenInterest=str(100+n),sumOpenInterestValue=str(10000+200*n))
        elif source=='taker':base.update(buyVol='2',sellVol='4',buySellRatio='0.5')
        else:base.update(longAccount='0.6',shortAccount='0.4',longShortRatio='1.5')
        rows.append(base)
    return rows


def test_archive_at_cap_stops_before_network(env):
    store,archive,clock=env
    archive.max_objects=1
    archive.put(b'previous-response')
    w,http=worker(env,statistics)
    report=w.cycle(['BTCUSDT'],['oi'],BASE)
    assert report['storage_blocked'] is True
    assert http.calls==[]
    assert report['snapshot_published'] is False


def test_archive_put_failure_stops_remaining_requests(env,monkeypatch):
    store,archive,clock=env
    def exhausted(body):raise OSError('quota exhausted')
    monkeypatch.setattr(archive,'put',exhausted)
    w,http=worker(env,statistics)
    report=w.cycle(['BTCUSDT'],['oi','taker'],BASE)
    assert report['storage_blocked'] is True
    assert len(http.calls)==1
    assert report['snapshot_published'] is False


def test_closed_stats_quantity_units_primitive_features_and_no_model_writes(env):
    w,http=worker(env,statistics)
    report=w.cycle(['BTCUSDT'],['oi','taker'],BASE)
    assert all(r['status']=='COMPLETE' for r in report['results'])
    assert len(http.calls)==2 and not report['model_evaluated']
    store,archive,clock=env
    output=derive_measurements(store,'BTCUSDT',clock(),archive)
    rows=store.feature_rows(output['feature_ids']);values={r['feature_name']:r for r in rows}
    assert values['oi_15m']['value']==pytest.approx((112/109-1)*100)
    assert values['oi_notional_15m']['value']!=values['oi_15m']['value']
    assert values['oi_4h']['value'] is None and values['oi_4h']['missing_reason']
    assert values['taker_ratio']['value']==0.5
    assert values['taker_ratio']['source_time']==stamp(BASE+timedelta(hours=1))
    assert store.journal.load()[0]==0 and store.journal.read('signals')==[]
    second=derive_measurements(store,'BTCUSDT',clock()+timedelta(seconds=1),archive)
    assert second['feature_ids']==output['feature_ids']
    assert store.journal.sql('SELECT COUNT(*) FROM csr_v33_evaluations').fetchone()[0]==0
    idle=w.cycle(['BTCUSDT'],['oi','taker'],BASE)
    assert all(r['status']=='NOT_DUE' for r in idle['results']) and len(http.calls)==2


def test_raw_upload_survives_failed_commit_and_retry_is_content_addressed(env,monkeypatch):
    store,archive,clock=env;w,http=worker(env,statistics)
    original=w.queue.finish
    monkeypatch.setattr(w.queue,'finish',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('private-secret')))
    assert w.cycle(['BTCUSDT'],['oi'],BASE)['results'][0]['status']=='UNAVAILABLE'
    state=w.queue.status('oi','BTCUSDT')
    assert state['revision']==0 and state['cursor']['next_time_ms']==MS
    blobs=list(archive.root.glob('*.json.gz'));assert len(blobs)==1
    clock.value+=timedelta(minutes=2)
    monkeypatch.setattr(w.queue,'finish',original)
    assert w.cycle(['BTCUSDT'],['oi'],BASE)['results'][0]['status']=='COMPLETE'
    assert len(list(archive.root.glob('*.json.gz')))==1
    failures=store.journal.read(store.kind('acquisition_failures'))
    assert 'private-secret' not in json.dumps(failures)


def test_overlapping_items_keep_original_availability_and_revision_appends(env):
    store,archive,clock=env;queue=AcquisitionQueue(store)
    queue.ensure('oi','BTCUSDT',MS,clock())
    blob=archive.put(b'[]');item={'key':str(MS),'effective_time':stamp(BASE),'values':{'raw_timestamp':MS,'quantity':'1','notional':'10'}}
    first=queue.claim('oi','BTCUSDT',clock())
    a=queue.finish(first,[item],first['cursor'],{'archive':blob},clock(),clock(),completed_at=clock())
    clock.value+=timedelta(seconds=1);second=queue.claim('oi','BTCUSDT',clock())
    b=queue.finish(second,[item],second['cursor'],{'archive':blob},clock(),clock(),completed_at=clock())
    assert b['new_records']==0 and a['record_ids']==b['record_ids']
    assert store.journal.record(store.kind('raw'),a['record_ids'][0])['availability_time']==stamp(NOW)
    item['values']['quantity']='2'
    clock.value+=timedelta(seconds=1);third=queue.claim('oi','BTCUSDT',clock())
    c=queue.finish(third,[item],third['cursor'],{'archive':blob},clock(),clock(),completed_at=clock())
    assert c['new_records']==1 and c['record_ids']!=a['record_ids']


def test_expired_or_replaced_lease_cannot_advance_cursor(env):
    store,archive,clock=env;queue=AcquisitionQueue(store)
    queue.ensure('oi','BTCUSDT',MS,clock());old=queue.claim('oi','BTCUSDT',clock())
    assert queue.claim('oi','BTCUSDT',clock()) is None
    clock.value+=timedelta(seconds=121)
    with pytest.raises(StaleAcquisition):queue.finish(old,[],old['cursor'],{'archive':archive.put(b'[]')},clock(),clock(),completed_at=clock())
    new=queue.claim('oi','BTCUSDT',clock())
    with pytest.raises(StaleAcquisition):queue.fail(old,clock(),'stale')
    queue.finish(new,[],new['cursor'],{'archive':archive.put(b'[]')},clock(),clock(),completed_at=clock())
    assert queue.status('oi','BTCUSDT')['revision']==1


def trade(ident,seconds,quantity='1',maker=False):
    return {'a':ident,'T':MS+seconds*1000,'p':'10','q':quantity,'m':maker,'f':ident,'l':ident}


def test_trade_pages_never_emit_partial_cvd_and_resume_by_inclusive_id(env):
    store,archive,clock=env;clock.value=BASE+timedelta(minutes=6)
    pages=[[trade(10,1),trade(11,2,'2',True)],[trade(12,250,'3'),trade(13,301,'999')]]
    w,http=worker(env,lambda source,params:pages.pop(0),page_limit=2)
    first=w.cycle(['BTCUSDT'],['perp_aggtrades'],BASE)
    assert first['results'][0]['status']=='PARTIAL'
    assert derive_measurements(store,'BTCUSDT',clock(),archive)['feature_ids']==[]
    clock.value+=timedelta(seconds=61)
    second=w.cycle(['BTCUSDT'],['perp_aggtrades'],BASE)
    assert second['results'][0]['status']=='COMPLETE'
    assert http.calls[1][1]=={'symbol':'BTCUSDT','limit':2,'fromId':12}
    features=derive_measurements(store,'BTCUSDT',clock(),archive)
    row=store.feature_rows(features['feature_ids'])[0]
    assert row['feature_name']=='perp_cvd_5m_quote' and row['value']==20
    raw=w.queue.item_at('perp_aggtrades_5m','BTCUSDT',clock(),clock())
    assert len(raw['payload']['values']['page_hashes'])==2
    assert w.queue.status('perp_aggtrades','BTCUSDT')['cursor']['next_id']==13


def test_trade_gap_or_missing_archive_blocks_finalization(env):
    store,archive,clock=env;clock.value=BASE+timedelta(minutes=6)
    pages=[[trade(1,1),trade(2,2)],[trade(4,3)]]
    w,http=worker(env,lambda source,params:pages.pop(0),page_limit=2)
    w.cycle(['BTCUSDT'],['spot_aggtrades'],BASE)
    saved=w.queue.status('spot_aggtrades','BTCUSDT')['cursor']
    clock.value+=timedelta(seconds=61)
    assert w.cycle(['BTCUSDT'],['spot_aggtrades'],BASE)['results'][0]['error_type']=='DataGap'
    assert w.queue.status('spot_aggtrades','BTCUSDT')['cursor']==saved
    assert derive_measurements(store,'BTCUSDT',clock(),archive)['feature_ids']==[]


def test_missing_prior_page_prevents_complete_window(env):
    store,archive,clock=env;clock.value=BASE+timedelta(minutes=6)
    pages=[[trade(1,1),trade(2,2)],[trade(3,3)]]
    w,_=worker(env,lambda source,params:pages.pop(0),page_limit=2)
    w.cycle(['BTCUSDT'],['perp_aggtrades'],BASE)
    saved=w.queue.status('perp_aggtrades','BTCUSDT')['cursor']
    archive.path(saved['page_ids'][0]).unlink()
    clock.value+=timedelta(seconds=61)
    result=w.cycle(['BTCUSDT'],['perp_aggtrades'],BASE)['results'][0]
    assert result['error_type']=='FileNotFoundError'
    assert w.queue.status('perp_aggtrades','BTCUSDT')['cursor']==saved
    assert derive_measurements(store,'BTCUSDT',clock(),archive)['feature_ids']==[]


def test_budget_and_429_halt_cycle_without_hammering_other_sources(env):
    w,http=worker(env,statistics,max_requests=1)
    report=w.cycle(['BTCUSDT'],['oi','taker'],BASE)
    assert len(http.calls)==1 and report['results'][1]['status']=='BUDGET_DEFERRED'
    def blocked(source,params):raise ProviderError(429,600)
    w,http=worker(env,blocked)
    report=w.cycle(['ETHUSDT'],['oi','taker'],BASE)
    assert report['provider_blocked'] and len(http.calls)==1
    assert w.queue.status('oi','ETHUSDT')['next_due']==stamp(NOW+timedelta(seconds=600))


def test_missing_auth_never_makes_request_or_leaks_key(monkeypatch):
    monkeypatch.delenv('CSR_BINANCE_DATA_API_KEY',raising=False)
    monkeypatch.setattr('requests.get',lambda *args,**kwargs:pytest.fail('no request without key'))
    budget=RequestBudget();transport=BinanceAcquisitionHTTP(budget)
    with pytest.raises(AuthRequired):transport.fetch('top_position',{'symbol':'BTCUSDT'})
    assert budget.calls==0


def test_archive_corruption_capacity_and_ephemeral_guard(env,monkeypatch):
    _,archive,_=env;ref=archive.put(b'[]')
    assert archive.put(b'[]')==ref
    archive.path(ref['sha256']).write_bytes(b'bad')
    with pytest.raises(Exception):archive.read(ref['sha256'])
    with pytest.raises(ValueError):archive.path('../escape')
    limited=FileArchive(archive.root/'limited',max_object_bytes=1)
    with pytest.raises(ValueError):limited.put(b'[]')
    monkeypatch.setenv('VERCEL','1')
    with pytest.raises(RuntimeError):FileArchive(archive.root/'ephemeral')


def test_authenticated_transport_uses_fixed_origin_no_redirects_and_weight_metadata(monkeypatch):
    class Response:
        status_code=200;headers={'X-MBX-USED-WEIGHT-1M':'42'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,*args):yield b'[]'
    calls=[]
    monkeypatch.setenv('CSR_BINANCE_DATA_API_KEY','private-do-not-log')
    monkeypatch.setattr('requests.get',lambda *args,**kwargs:calls.append((args,kwargs)) or Response())
    http=BinanceAcquisitionHTTP(RequestBudget())
    payload,body,_=http.fetch('top_position',{'symbol':'BTCUSDT'})
    assert calls[0][0][0]=='https://fapi.binance.com/futures/data/topLongShortPositionRatio'
    assert calls[0][1]['allow_redirects'] is False
    assert calls[0][1]['headers']['X-MBX-APIKEY']=='private-do-not-log'
    assert payload==[] and body==b'[]' and 'private' not in json.dumps(http.measurements)
    assert http.measurements['last_server_used_weight_1m']==42


def test_derivation_requires_intact_raw_evidence(env):
    store,archive,clock=env;w,_=worker(env,statistics)
    w.cycle(['BTCUSDT'],['oi'],BASE)
    record=w.queue.item_at('oi','BTCUSDT',clock(),clock())
    archive.path(record['payload']['archive']['sha256']).unlink()
    with pytest.raises(FileNotFoundError):derive_measurements(store,'BTCUSDT',clock(),archive)


def test_funding_and_premium_share_no_settlement_assumption(env):
    store,archive,clock=env
    def response(source,params):
        if source=='premium_mark_index':
            return {'symbol':'BTCUSDT','time':int(clock().timestamp()*1000),'markPrice':'101','indexPrice':'100',
                    'lastFundingRate':'0.0001','nextFundingTime':int((clock()+timedelta(hours=2)).timestamp()*1000)}
        return []
    w,http=worker(env,response)
    report=w.cycle(['BTCUSDT'],['funding_settlement','premium_mark_index'],BASE)
    assert [s for s,_ in http.calls]==['premium_mark_index','funding_settlement']
    assert all(r['status']=='COMPLETE' for r in report['results'])
    assert w.queue.status('funding_settlement','BTCUSDT')['next_due']==stamp(clock()+timedelta(hours=2,seconds=60))
    rows=store.feature_rows(derive_measurements(store,'BTCUSDT',clock(),archive)['feature_ids'])
    values={r['feature_name']:r['value'] for r in rows}
    assert 'funding_rate_settled' not in values and values['funding_rate_quote']==0.0001
    assert values['mark_index_basis_fraction']==pytest.approx(0.01)


def test_live_taker_shape_omits_symbol_and_has_bounded_leading_overlap():
    cursor={'next_time_ms':MS+300000}
    params,plan=request_plan('taker','BTCUSDT',cursor,BASE+timedelta(minutes=16))
    assert params['endTime']==MS+900000
    data=[{'timestamp':when,'buyVol':'2','sellVol':'4','buySellRatio':'0.5'} for when in range(MS,MS+900001,300000)]
    rows,next_cursor,complete=normalize_statistics('taker','BTCUSDT',data,plan,cursor)
    assert complete and [r['values']['raw_timestamp'] for r in rows]==[MS+300000,MS+600000]
    assert next_cursor['next_time_ms']==MS+900000


def test_snapshot_clock_lead_is_withheld_not_backdated(env,monkeypatch):
    store,archive,clock=env;original=clock()
    payload={'symbol':'BTCUSDT','time':int(original.timestamp()*1000)+500,'markPrice':'101','indexPrice':'100',
             'lastFundingRate':'0.0001','nextFundingTime':int((original+timedelta(hours=2)).timestamp()*1000)}
    monkeypatch.setattr('src.v33.acquisition_worker.time.sleep',lambda seconds:setattr(clock,'value',clock()+timedelta(seconds=seconds)))
    w,_=worker(env,lambda source,params:payload)
    assert w.cycle(['BTCUSDT'],['premium_mark_index'],BASE)['results'][0]['status']=='COMPLETE'
    receipt=store.journal.read(store.kind('acquisitions'))[0]
    assert receipt['received_at']==stamp(original) and receipt['availability_time']>receipt['received_at']
    record=w.queue.item_at('premium_mark_index','BTCUSDT',clock(),clock())
    assert record['effective_time']<=record['availability_time']


def test_large_clock_lead_remains_quarantined(env,monkeypatch):
    store,archive,clock=env
    payload={'symbol':'BTCUSDT','time':int(clock().timestamp()*1000)+10000,'markPrice':'101','indexPrice':'100',
             'lastFundingRate':'0.0001','nextFundingTime':int((clock()+timedelta(hours=2)).timestamp()*1000)}
    monkeypatch.setattr('src.v33.acquisition_worker.time.sleep',lambda seconds:pytest.fail('never sleep for large skew'))
    w,_=worker(env,lambda source,params:payload)
    result=w.cycle(['BTCUSDT'],['premium_mark_index'],BASE)['results'][0]
    assert result['error_type']=='ClockSkew' and result['status']=='UNAVAILABLE'
    assert w.queue.status('premium_mark_index','BTCUSDT')['revision']==0


@given(st.lists(st.tuples(st.integers(1,10000),st.booleans()),min_size=1,max_size=50))
@settings(max_examples=100,derandomize=True)
def test_property_trade_sign_and_exact_quote_delta(rows):
    payload=[trade(i,i,str(quantity),maker) for i,(quantity,maker) in enumerate(rows,1)]
    cursor={'next_id':None,'quote_delta':'0','buy_quote':'0','sell_quote':'0','page_ids':[],'trade_count':0,'window_start_ms':MS}
    plan={'limit':500,'start_ms':MS,'cutoff_ms':MS+300000}
    items,_,complete=normalize_trades('spot_aggtrades',payload,plan,cursor,'a'*64)
    assert complete
    expected=sum(Decimal(q)*10*(-1 if maker else 1) for q,maker in rows)
    assert Decimal(items[0]['values']['quote_delta'])==expected


@pytest.mark.parametrize('source',['oi','taker','top_account','top_position','global_accounts'])
def test_statistics_gap_future_and_wrong_symbol_are_not_silently_accepted(source):
    cursor={'next_time_ms':MS};params,plan=request_plan(source,'BTCUSDT',cursor,NOW)
    data=statistics(source,params)
    with pytest.raises(DataGap):normalize_statistics(source,'BTCUSDT',data[1:],plan,cursor)
    data[0]['symbol']='WRONG'
    with pytest.raises(ValueError):normalize_statistics(source,'BTCUSDT',data,plan,cursor)
