from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3

import pytest
from hypothesis import given,settings,strategies as st
from src.research_journal import ResearchJournal,RevisionConflict
from src.v33.contracts import RawObservation,Feature,stamp,snapshot,evaluation_key,REFERENCE_FEATURES
from src.v33.registry import defaults
from src.v33.journal import ContractStore,install_local
from src.v33.families import aggregate
from src.v33.fsm import advance,initial,assert_contract,validate_edge
from src.v33.causal import past_percentile,epoch_time
from src.v33.sources import SOURCES,observed_times

BASE=datetime(2026,10,30,tzinfo=timezone.utc)
POLICY=defaults()
PARAMS=POLICY['parameters']
COMPONENTS=[f+'.'+c for f,items in PARAMS['component_weights'].items() for c in items]


def vector(now,score=100,**changes):
    values={name:score for name in COMPONENTS}
    values.update(return_24h=100,return_24h_p95=30,return_7d=80,quote_volume_24h=1000,quote_volume_median_30d=100,
        oi_15m=-4,oi_1h=20,oi_4h=20,oi_1h_p75=5,oi_4h_p75=10,
        efficiency_collapse_score=90,oi_15m_retreat_confirmed=True,perp_cvd=-100,
        spot_cvd=-100,taker_ratio=0.78,lower_high_candidate=True,lower_high_confirmed=True,
        price_breakdown_confirmed=True,dynamic_quality=100,execution_quality=100,squeeze_risk=0,
        close_5m=100,model_maturity='MATURE',source_integrity=True,
        bar_5m=stamp(now),bar_15m=stamp(now.replace(minute=(now.minute//15)*15,second=0,microsecond=0)))
    values.update(changes)
    return values


def simple_rows(values,now):
    return [dict(feature_name=name,source_time=stamp(now),asof_time=stamp(now),availability_time=stamp(now),symbol='A',value=value) for name,value in values.items()]


def frame(store,now,values,revision='initial',available=None):
    available=available or now
    raw=store.put_raw(RawObservation('fixture','A',stamp(now),stamp(now),stamp(available),stamp(available),revision,values))
    past=now-timedelta(minutes=5)
    reference=store.put_raw(RawObservation('fixture-reference','A',stamp(past),stamp(past),stamp(available),stamp(available),revision,values))
    refs=REFERENCE_FEATURES
    return [store.put_feature(Feature(name,'fixture-feature-1','A',stamp(now),stamp(past if name in refs else now),stamp(available),value,
        (reference if name in refs else raw,), 'fixture_missing' if value is None else None,
        (reference,) if name in refs else ())) for name,value in values.items()]


@pytest.fixture
def store(tmp_path):
    journal=ResearchJournal(tmp_path/'v33.db')
    install_local(journal);install_local(journal)
    result=ContractStore(journal,'REPLAY','fixture')
    result.put_rule(POLICY)
    yield result
    journal.close()


def replay_chain(store,scores=100):
    output=[]
    for minutes in (5,10,15,20,30,45,50,55,60):
        now=BASE+timedelta(minutes=minutes)
        score=65 if scores=='multistep' and minutes in (50,55) else 100
        values=vector(now,score=score,oi_1h=20 if minutes<=20 else -6)
        ids=frame(store,now,values)
        output.append(store.evaluate('A',now,POLICY['rule_version'],ids))
    return output


def test_full_causal_replay_and_idempotent_parent_chain(store):
    results=replay_chain(store)
    assert [r['phase'] for r in results]==['NORMAL','PUMP','PUMP','LEVERAGE_EXPANSION','LEVERAGE_EXPANSION','SATURATION','DISTRIBUTION_SUSPECTED','DELEVERAGING','MARKDOWN']
    assert results[-1]['trade_state']=='SHORT_TRIGGERED'
    events={e['event_id']:e for r in results for e in r['events']}
    trigger=next(e for e in events.values() if e['event_type']=='SHORT_TRIGGERED')
    ready=events[trigger['parent_event_id']];setup=events[ready['parent_event_id']]
    assert ready['event_type']=='SHORT_READY' and setup['event_type']=='SHORT_SETUP'
    assert results[-1]['signal_chain_id']==trigger['chain_id']
    assert results[-1]['pump_anchor_time']==stamp(BASE+timedelta(minutes=10))
    expires={r['expires_at'] for r in results if r['expires_at']}
    assert len(expires)==1  # Same setup does not renew TTL on every READY.
    last=results[-1]
    ids=[json.loads(r[0])['feature_id'] for r in store.journal.sql('SELECT payload FROM csr_v33_features WHERE asof_time=?',(last['evaluation_time'],)).fetchall()]
    count=store.journal.sql('SELECT COUNT(*) FROM csr_v33_events').fetchone()[0]
    assert store.evaluate('A',last['evaluation_time'],POLICY['rule_version'],list(reversed(ids)))==last
    assert store.journal.sql('SELECT COUNT(*) FROM csr_v33_events').fetchone()[0]==count
    assert store.journal.load()[0]==0  # Research never advances the live model head.


def test_same_evaluation_appends_three_levels_in_order(store):
    result=replay_chain(store,'multistep')[-1]
    trade=[e for e in result['events'] if e['event_type'].startswith('SHORT_')]
    assert [e['event_type'] for e in trade]==['SHORT_SETUP','SHORT_READY','SHORT_TRIGGERED']
    assert trade[1]['parent_event_id']==trade[0]['event_id']
    assert trade[2]['parent_event_id']==trade[1]['event_id']
    assert trade[0]['sequence_no']<trade[1]['sequence_no']<trade[2]['sequence_no']


def test_namespace_isolation_champion_guard_and_immutable_registry(store):
    ids=frame(store,BASE,vector(BASE))
    other=ContractStore(store.journal,'BACKTEST','different')
    with pytest.raises(ValueError):other.evaluate('A',BASE,POLICY['rule_version'],ids)
    with pytest.raises(ValueError):ContractStore(store.journal,'PRODUCTION','live')
    prod=ContractStore(store.journal,'PRODUCTION','live',allow_production=True)
    with pytest.raises(ValueError):prod.put_rule(POLICY)
    revised=deepcopy(POLICY);revised['parameters']['trade']['ready_score']=1
    with pytest.raises(ValueError):store.put_rule(revised)


def test_append_only_sql_blocks_mutation_and_retries_survive_restart(store):
    ids=frame(store,BASE,vector(BASE));original=store.evaluate('A',BASE,POLICY['rule_version'],ids)
    for table in ('csr_v33_features','csr_v33_evaluations'):
        with pytest.raises(sqlite3.IntegrityError):
            with store.journal.transaction():store.journal.sql(f'DELETE FROM {table}')
    reopened=ResearchJournal(store.journal.db.execute('PRAGMA database_list').fetchone()[2])
    try:
        second=ContractStore(reopened,'REPLAY','fixture')
        assert second.evaluate('A',BASE,POLICY['rule_version'],ids)==original
    finally:reopened.close()


def test_late_data_and_correction_do_not_rewrite_history(store):
    ids=frame(store,BASE,vector(BASE))
    original=store.evaluate('A',BASE,POLICY['rule_version'],ids)
    late=frame(store,BASE+timedelta(minutes=5),vector(BASE+timedelta(minutes=5)),available=BASE+timedelta(minutes=10))
    with pytest.raises(ValueError):store.evaluate('A',BASE+timedelta(minutes=5),POLICY['rule_version'],late)
    assert store.asof('A','oi_1h','fixture-feature-1',BASE+timedelta(minutes=5))['feature_id'] in ids
    corrected=frame(store,BASE,vector(BASE,oi_1h=999),revision='corrected')
    with pytest.raises(ValueError):store.evaluate('A',BASE,POLICY['rule_version'],corrected)
    other=ContractStore(store.journal,'REPLAY','corrected-history')
    revised_ids=frame(other,BASE,vector(BASE,oi_1h=999),revision='corrected')
    corrected_result=other.evaluate('A',BASE,POLICY['rule_version'],revised_ids,supersedes=original['evaluation_id'])
    assert corrected_result['feature_snapshot_hash']!=original['feature_snapshot_hash']
    assert corrected_result['supersedes_evaluation_id']==original['evaluation_id']
    assert store.evaluate('A',BASE,POLICY['rule_version'],ids)==original


def test_unknown_effective_time_bad_data_and_refreshed_clock_cannot_be_features(store):
    raw=store.put_raw(RawObservation('unknown','A',1,None,stamp(BASE),stamp(BASE),'1',{}))
    with pytest.raises(ValueError):store.put_feature(Feature('oi','1','A',stamp(BASE),stamp(BASE),stamp(BASE),1,(raw,)))
    raw=store.put_raw(RawObservation('known','A',1,stamp(BASE),stamp(BASE),stamp(BASE),'1',{}))
    future=stamp(BASE+timedelta(minutes=5))
    with pytest.raises(ValueError):store.put_feature(Feature('oi','1','A',future,future,future,1,(raw,)))


def test_cas_and_invalid_parent_roll_back_the_whole_evaluation(store):
    ids=frame(store,BASE,vector(BASE))
    with pytest.raises(RevisionConflict):store.evaluate('A',BASE,POLICY['rule_version'],ids,expected_revision=99)
    assert store.journal.sql('SELECT COUNT(*) FROM csr_v33_evaluations').fetchone()[0]==0
    state=initial();state.update(phase='MARKDOWN',trade_state='SHORT_READY',chain_id='missing',last_event_id='absent')
    with store.journal.transaction():
        store.journal.sql('INSERT INTO csr_v33_heads VALUES(?,?,?,?,?,?,?)',('REPLAY','fixture','A',POLICY['rule_version'],1,stamp(BASE-timedelta(minutes=5)),json.dumps(state)))
    with pytest.raises(ValueError,match='parent'):store.evaluate('A',BASE,POLICY['rule_version'],ids)
    assert store.journal.sql('SELECT COUNT(*) FROM csr_v33_evaluations').fetchone()[0]==0


@given(st.dictionaries(st.sampled_from(COMPONENTS),st.one_of(st.none(),st.floats(min_value=0,max_value=100,allow_nan=False)),max_size=len(COMPONENTS)))
@settings(max_examples=150,derandomize=True)
def test_property_family_coverage_score_breadth_and_strong(values):
    result=aggregate(values,PARAMS)
    valid=[f for f in result['families'].values() if f['status']=='VALID']
    assert (result['distribution_score'] is None)==(len(valid)<3)
    if result['distribution_score'] is not None:assert 0<=result['distribution_score']<=100.00000001
    for family in result['families'].values():
        assert 0<=family['coverage']<=1
        if family['status']!='VALID':assert not family['strong']
    assert result['score_breadth']==('INSUFFICIENT' if len(valid)<3 else 'REDUCED' if len(valid)==3 else 'FULL')


@given(st.lists(st.tuples(st.integers(0,100),st.integers(0,100),st.booleans()),min_size=1,max_size=45))
@settings(max_examples=100,derandomize=True,deadline=None)
def test_property_temporal_sequences_parents_gates_and_terminal_xor(sequence):
    state=initial();events_by_id={};terminals={}
    for i,(score,risk,missing) in enumerate(sequence,1):
        now=BASE+timedelta(minutes=5*i)
        values=vector(now,score=score,squeeze_risk=risk,oi_1h=20 if i<6 else -6)
        if missing:
            for name in COMPONENTS:
                if name.startswith(('F3','F4','F5')):values[name]=None
        rows=simple_rows(values,now)
        state,result,events=advance(state,values,rows,now,str(i),PARAMS)
        assert_contract(result,events)
        for event in events:
            kind=event['event_type']
            if kind in {'SHORT_READY','SHORT_TRIGGERED'}:
                parent=events_by_id[event['parent_event_id']]
                assert parent['event_type']==('SHORT_SETUP' if kind=='SHORT_READY' else 'SHORT_READY')
                assert parent['chain_id']==event['chain_id']
            if kind in {'INVALIDATED','EXPIRED'}:
                assert event['chain_id'] not in terminals
                terminals[event['chain_id']]=kind
            events_by_id[event['event_id']]=event


@given(st.integers(min_value=-1000,max_value=1000))
@settings(max_examples=50,derandomize=True)
def test_property_availability_and_same_input_idempotency(offset):
    with TemporaryDirectory() as folder:
        j=ResearchJournal(Path(folder)/'test.db');install_local(j)
        s=ContractStore(j,'BACKTEST','generated');s.put_rule(POLICY)
        when=BASE+timedelta(seconds=max(0,offset))
        ids=frame(s,BASE,vector(BASE),available=when)
        if when>BASE:
            with pytest.raises(ValueError):s.evaluate('A',BASE,POLICY['rule_version'],ids)
        else:
            first=s.evaluate('A',BASE,POLICY['rule_version'],ids)
            assert first==s.evaluate('A',BASE,POLICY['rule_version'],ids)
            assert j.sql('SELECT COUNT(*) FROM csr_v33_evaluations').fetchone()[0]==1
        j.close()


PHASES=['NORMAL','PUMP','LEVERAGE_EXPANSION','SATURATION','DISTRIBUTION_SUSPECTED','DELEVERAGING','MARKDOWN']
PHASE_LEGAL={('NORMAL','PUMP'),('PUMP','NORMAL'),('PUMP','LEVERAGE_EXPANSION'),('LEVERAGE_EXPANSION','SATURATION'),('SATURATION','LEVERAGE_EXPANSION'),('SATURATION','DISTRIBUTION_SUSPECTED'),('DISTRIBUTION_SUSPECTED','LEVERAGE_EXPANSION'),('DISTRIBUTION_SUSPECTED','DELEVERAGING'),('DELEVERAGING','MARKDOWN')}
TRADES=['WATCH','SHORT_SETUP','SHORT_READY','SHORT_TRIGGERED','INVALIDATED','EXPIRED']
TRADE_LEGAL={('WATCH','SHORT_SETUP'),('SHORT_SETUP','SHORT_READY'),('SHORT_SETUP','INVALIDATED'),('SHORT_SETUP','EXPIRED'),('SHORT_READY','SHORT_TRIGGERED'),('SHORT_READY','INVALIDATED'),('SHORT_READY','EXPIRED'),('SHORT_TRIGGERED','INVALIDATED'),('SHORT_TRIGGERED','EXPIRED')}


@pytest.mark.parametrize('previous,current',[(a,b) for a in PHASES for b in PHASES])
def test_full_phase_transition_matrix(previous,current):
    if previous==current or (previous,current) in PHASE_LEGAL:validate_edge(previous,current,phase=True)
    else:
        with pytest.raises(ValueError):validate_edge(previous,current,phase=True)


@pytest.mark.parametrize('previous,current',[(a,b) for a in TRADES for b in TRADES])
def test_full_trade_transition_matrix(previous,current):
    if previous==current or (previous,current) in TRADE_LEGAL:validate_edge(previous,current)
    else:
        with pytest.raises(ValueError):validate_edge(previous,current)


@pytest.mark.parametrize('phase,trade',[(a,b) for a in PHASES for b in TRADES])
def test_full_coupling_matrix(phase,trade):
    allowed=(trade in {'WATCH','INVALIDATED','EXPIRED'} or
        trade=='SHORT_SETUP' and phase in {'DISTRIBUTION_SUSPECTED','DELEVERAGING','MARKDOWN'} or
        trade=='SHORT_READY' and phase in {'DELEVERAGING','MARKDOWN'} or trade=='SHORT_TRIGGERED' and phase=='MARKDOWN')
    r=dict(phase=phase,trade_state=trade,executable=True,active_blocks=[],display_gate='OPEN',score_breadth='FULL')
    if allowed:assert_contract(r,[])
    else:
        with pytest.raises(ValueError):assert_contract(r,[])


def test_timestamp_units_start_vs_end_and_past_percentile():
    milliseconds=int(BASE.timestamp()*1000)
    assert epoch_time(milliseconds,'ms')==epoch_time(milliseconds*1000,'us')
    with pytest.raises(ValueError):observed_times('taker',milliseconds,BASE,300)
    assert observed_times('taker',milliseconds,BASE+timedelta(minutes=5),300)['effective_time']==stamp(BASE+timedelta(minutes=5))
    assert SOURCES['top_account'].auth_requirement==SOURCES['top_position'].auth_requirement=='X-MBX-APIKEY'
    observations=[{'asof_time':stamp(BASE-timedelta(minutes=1)),'availability_time':stamp(BASE),'value':2},
                  {'asof_time':stamp(BASE),'availability_time':stamp(BASE),'value':999},
                  {'asof_time':stamp(BASE-timedelta(minutes=2)),'availability_time':stamp(BASE+timedelta(seconds=1)),'value':999}]
    assert past_percentile(observations,BASE,95,1)==2


def test_reduced_breadth_mature_caps_and_new_listing_exception():
    now=BASE
    state=initial();state['phase']='DELEVERAGING'
    values=vector(now,oi_1h=-6)
    for name in COMPONENTS:
        if name.startswith(('F4','F5')):values[name]=None
    _,mature,events=advance(state,values,simple_rows(values,now),now,'mature',PARAMS)
    assert mature['distribution_score']==100 and mature['score_breadth']=='REDUCED'
    assert mature['trade_state']=='SHORT_SETUP' and 'DATA' in mature['active_blocks']
    values['model_maturity']='NEW_LISTING'
    _,new,events=advance(state,values,simple_rows(values,now),now,'new',PARAMS)
    assert new['trade_state']=='SHORT_TRIGGERED' and new['phase']=='MARKDOWN'
    assert [e['event_type'] for e in events if e['event_type'].startswith('SHORT_')]==['SHORT_SETUP','SHORT_READY','SHORT_TRIGGERED']
    values['dynamic_quality']=84
    _,blocked,_=advance(state,values,simple_rows(values,now),now,'poor',PARAMS)
    assert blocked['trade_state']=='SHORT_SETUP'


def test_terminal_priority_ttl_and_squeeze_distinct_observations():
    now=BASE
    state=initial();state.update(phase='MARKDOWN',trade_state='SHORT_READY',chain_id='a',last_event_id='ready',expires_at=stamp(now))
    values=vector(now,oi_1h=-6,invalidate_setup=True)
    closed,r,events=advance(state,values,simple_rows(values,now),now,'terminal',PARAMS)
    assert r['trade_state']=='INVALIDATED' and [e['event_type'] for e in events]==['INVALIDATED']
    _,r,events=advance(closed,values,simple_rows(values,now),now,'later',PARAMS)
    assert events==[] and r['trade_state']=='INVALIDATED'
    values['invalidate_setup']=False
    _,r,events=advance(state,values,simple_rows(values,now),now,'expiry',PARAMS)
    assert r['trade_state']=='EXPIRED' and len(events)==1
    state=initial();values=vector(now,squeeze_risk=80)
    state,_,_=advance(state,values,simple_rows(values,now),now,'squeeze',PARAMS)
    for i in (1,1,2):
        later=now+timedelta(minutes=5*i);values=vector(later,squeeze_risk=0)
        state,r,_=advance(state,values,simple_rows(values,later),later,str(i),PARAMS)
        assert ('SQUEEZE' in r['active_blocks'])==(i!=2)


def test_breadth_loss_invalidates_existing_ready_and_stale_core_blocks_trigger():
    now=BASE;values=vector(now,oi_1h=-6)
    state=initial();state.update(phase='MARKDOWN',trade_state='SHORT_READY',chain_id='a',last_event_id='ready')
    for name in COMPONENTS:
        if name.startswith(('F4','F5')):values[name]=None
    _,result,_=advance(state,values,simple_rows(values,now),now,'breadth-loss',PARAMS)
    assert result['trade_state']=='INVALIDATED'
    values=vector(now,oi_1h=-6)
    rows=simple_rows(values,now)
    next(r for r in rows if r['feature_name']=='oi_15m')['source_time']=stamp(now-timedelta(minutes=10))
    _,result,_=advance(state,values,rows,now,'stale',PARAMS)
    assert 'DATA' in result['active_blocks'] and result['trade_state']!='SHORT_TRIGGERED'


def test_unknown_pump_inputs_do_not_count_as_a_false_condition():
    state=initial();state['phase']='PUMP'
    for minutes in (15,30,45,60):
        now=BASE+timedelta(minutes=minutes)
        values={'bar_15m':stamp(now),'bar_5m':stamp(now)}
        state,result,_=advance(state,values,simple_rows(values,now),now,str(minutes),PARAMS)
        assert result['phase']=='PUMP' and result['trade_state']=='WATCH'


def test_percentile_without_past_reference_lineage_is_rejected(store):
    raw=store.put_raw(RawObservation('fixture','A',stamp(BASE),stamp(BASE),stamp(BASE),stamp(BASE),'1',{}))
    feature=store.put_feature(Feature('return_24h_p95','1','A',stamp(BASE),stamp(BASE),stamp(BASE),25,(raw,)))
    with pytest.raises(ValueError,match='reference'):store.evaluate('A',BASE,POLICY['rule_version'],[feature])
    with pytest.raises(ValueError,match='strictly precede'):
        store.put_feature(Feature('return_24h_p95','1','A',stamp(BASE),stamp(BASE),stamp(BASE),25,(raw,),reference_source_ids=(raw,)))


def test_gate_open_is_not_a_trade_instruction_and_price_confirm_is_ready_specific():
    values=vector(BASE,price_breakdown_confirmed=False)
    _,watch,_=advance(initial(),values,simple_rows(values,BASE),BASE,'watch',PARAMS)
    assert watch['executable'] and not watch['signal_eligible']
    assert watch['display_gate']=='OPEN' and watch['trade_state']=='WATCH'
    state=initial();state.update(phase='DELEVERAGING',trade_state='SHORT_READY',chain_id='a',last_event_id='ready')
    _,ready,_=advance(state,values,simple_rows(values,BASE),BASE,'ready',PARAMS)
    assert ready['active_blocks']==['PRICE_CONFIRM'] and not ready['executable']
