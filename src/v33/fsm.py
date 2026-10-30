"""Independent phase/trade machines driven by versioned experimental predicates.

Component scoring, structure detectors and quality estimators are upstream
research producers, not invented formulas in this contract implementation.
"""
from copy import deepcopy
from datetime import timedelta
from .contracts import digest, stamp
from ..retry_policy import utc
from .families import aggregate, numeric

PHASE_EDGES = {
 'NORMAL': {'PUMP'}, 'PUMP': {'NORMAL','LEVERAGE_EXPANSION'},
 'LEVERAGE_EXPANSION': {'SATURATION'},
 'SATURATION': {'LEVERAGE_EXPANSION','DISTRIBUTION_SUSPECTED'},
 'DISTRIBUTION_SUSPECTED': {'LEVERAGE_EXPANSION','DELEVERAGING'},
 'DELEVERAGING': {'MARKDOWN'}, 'MARKDOWN': set()
}
COUPLING = {
 'WATCH': set(PHASE_EDGES),
 'SHORT_SETUP': {'DISTRIBUTION_SUSPECTED','DELEVERAGING','MARKDOWN'},
 'SHORT_READY': {'DELEVERAGING','MARKDOWN'}, 'SHORT_TRIGGERED': {'MARKDOWN'}
}
TERMINALS = {'INVALIDATED','EXPIRED'}
TRADE_EDGES = {'WATCH': {'SHORT_SETUP'}, 'SHORT_SETUP': {'SHORT_READY',*TERMINALS},
               'SHORT_READY': {'SHORT_TRIGGERED',*TERMINALS}, 'SHORT_TRIGGERED': set(TERMINALS),
               'INVALIDATED': set(), 'EXPIRED': set()}
BLOCK_ORDER = ('DATA','LIQUIDITY','SQUEEZE','PRICE_CONFIRM')
STATE_VERSION = 'v33-state-1'


def validate_edge(previous,current,phase=False):
    edges=PHASE_EDGES if phase else TRADE_EDGES
    if previous not in edges or (current!=previous and current not in edges[previous]):
        raise ValueError('illegal phase edge' if phase else 'illegal trade edge')


def initial():
    return {'state_version':STATE_VERSION,'phase':'NORMAL','trade_state':'WATCH','cycle':0,
            'chain_id':None,'last_event_id':None,'expires_at':None,'counters':{},
            'squeeze_blocked':False,'squeeze_clear_count':0,'squeeze_last_observation':None,
            'pump_anchor_time':None}


def _confirmed(state, name, condition, count, seconds, bar):
    if bar is None:
        return False
    bar=stamp(bar)
    previous=state['counters'].get(name)
    if previous and previous['bar']==bar:
        # Re-evaluating a revised snapshot must not count another observation.
        if not condition:previous['count']=0
        return condition and previous['count']>=count
    consecutive=previous and (utc(bar)-utc(previous['bar'])).total_seconds()==seconds
    value=(previous['count'] if consecutive else 0)+1 if condition else 0
    state['counters'][name]={'bar':bar,'count':value}
    return value>=count


def advance(old, values, feature_rows, now, key, parameters):
    now=stamp(now)
    state=deepcopy(old or initial())
    if state['state_version']!=STATE_VERSION:
        raise ValueError('unsupported V3 state version')
    result=aggregate(values,parameters)
    p,t,g=parameters['phase'],parameters['trade'],parameters['gate']
    families=result['families']; score=result['distribution_score']; strong=result['strong_families']
    def val(name):
        value=values.get(name)
        return value if numeric(value) else None
    def ge(name, threshold):
        v=val(name);return v is not None and v>=threshold
    def lt(name, threshold):
        v=val(name);return v is not None and v<threshold
    def truth(name):return values.get(name) is True
    def family(name, minimum):
        f=families[name];return f['status']=='VALID' and f['score']>=minimum
    def confirm(name, condition, n):
        observation=p['confirmation_observations'][name]
        bar=values.get(observation['feature'])
        if bar is None or stamp(bar)>now:
            return False
        return _confirmed(state,name,condition,n,observation['seconds'],bar)
    events=[]
    def emit(kind, chain=None, parent=None, reason=None):
        seq=len(events)+1
        event_id=digest([key,kind,seq])
        events.append({'event_id':event_id,'event_type':kind,'sequence_no':seq,
                       'chain_id':chain,'parent_event_id':parent,'phase':state['phase'],
                       'evaluation_time':now,'state_version':STATE_VERSION,'reason':reason})
        return event_id

    r24=ge('return_24h',val('return_24h_p95')) if val('return_24h') is not None and val('return_24h_p95') is not None else None
    r7=ge('return_7d',p['pump_7d_return']) if val('return_7d') is not None else None
    returns=True if r24 is True or r7 is True else False if r24 is False and r7 is False else None
    volume=ge('quote_volume_24h',val('quote_volume_median_30d')*p['volume_multiple']) if val('quote_volume_24h') is not None and val('quote_volume_median_30d') is not None and val('quote_volume_median_30d')>0 else None
    pump=True if returns is True and volume is True else False if returns is False or volume is False else None
    phase=state['phase']; target=None
    if phase=='NORMAL' and confirm('pump',pump is True,p['pump_confirm']):target='PUMP'
    elif phase=='PUMP':
        expanding=ge('oi_1h',0) and val('oi_1h')>0 and (
            (val('oi_1h_p75') is not None and ge('oi_1h',val('oi_1h_p75'))) or
            (val('oi_4h_p75') is not None and ge('oi_4h',val('oi_4h_p75'))))
        if confirm('leverage',expanding,p['leverage_confirm']):target='LEVERAGE_EXPANSION'
        elif confirm('pump_reset',pump is False,p['pump_reset']):target='NORMAL'
    elif phase=='LEVERAGE_EXPANSION':
        # Unknown OI is not evidence that leverage has not retreated.
        oi_complete=all(val(n) is not None for n in ('oi_15m','oi_1h','oi_4h'))
        if confirm('saturation',ge('efficiency_collapse_score',p['saturation_score']) and oi_complete and not all(lt(n,0) for n in ('oi_15m','oi_1h','oi_4h')),p['saturation_confirm']):target='SATURATION'
    elif phase=='SATURATION':
        if score is not None and score>=p['distribution_score'] and strong>=t['strong_families'] and family('F1',p['distribution_f1']):target='DISTRIBUTION_SUSPECTED'
        elif confirm('saturation_recovery',truth('efficiency_recovery') and truth('oi_acceleration_positive') and truth('flow_positive'),p['recovery_confirm']):target='LEVERAGE_EXPANSION'
    elif phase=='DISTRIBUTION_SUSPECTED':
        if truth('oi_15m_retreat_confirmed') and family('F2',p['leverage_f2']) and (lt('oi_1h',0) or lt('perp_cvd',0) or lt('taker_ratio',p['retreat_taker'])):target='DELEVERAGING'
        elif confirm('distribution_recovery',score is not None and score<p['recovery_score'] and truth('new_high') and truth('oi_acceleration_positive') and truth('flow_positive'),p['recovery_confirm']):target='LEVERAGE_EXPANSION'
    elif phase=='DELEVERAGING' and truth('price_breakdown_confirmed') and family('F1',p['markdown_f1']) and family('F2',p['markdown_f2']):target='MARKDOWN'
    # Mature reduced breadth is capped at DISTRIBUTION_SUSPECTED by the spec.
    new_exception=(values.get('model_maturity')=='NEW_LISTING' and t['allow_new_listing_reduced']
                   and all(family(n,t['new_listing_family']) for n in ('F1','F2','F3'))
                   and ge('dynamic_quality',t['new_listing_dynamic']) and ge('execution_quality',t['new_listing_execution']))
    if target in {'DELEVERAGING','MARKDOWN'} and result['score_breadth']=='REDUCED' and not new_exception:
        target=None
    if target:
        validate_edge(phase,target,phase=True)
        state['phase']=target
        if target=='PUMP' and phase=='NORMAL':state['pump_anchor_time']=now
        if target=='NORMAL':state['pump_anchor_time']=None
        emit('PHASE_'+target)

    # Squeeze release uses distinct chronological observations, not HTTP retries.
    observation=values.get('bar_5m')
    risk=val('squeeze_risk')
    if risk is not None and risk>=g['squeeze_on']:
        state['squeeze_blocked']=True;state['squeeze_clear_count']=0
    elif observation and (state['squeeze_last_observation'] is None or stamp(observation)>state['squeeze_last_observation']):
        state['squeeze_clear_count']=state['squeeze_clear_count']+1 if risk is not None and risk<=g['squeeze_off'] else 0
        if state['squeeze_clear_count']>=g['squeeze_clear_observations']:state['squeeze_blocked']=False
    if observation and (state['squeeze_last_observation'] is None or stamp(observation)>state['squeeze_last_observation']):
        state['squeeze_last_observation']=stamp(observation)
    rows={row['feature_name']:row for row in feature_rows}
    drift=False
    for name in g['fast_core']:
        row=rows.get(name)
        if not row or row['value'] is None or (utc(now)-utc(row['source_time'])).total_seconds()>g['fast_core_drift_seconds']:
            drift=True
    dynamic_required=t['trigger_dynamic'] if state['trade_state']=='SHORT_TRIGGERED' else t['ready_dynamic']
    execution_required=t['trigger_execution'] if state['trade_state']=='SHORT_TRIGGERED' else t['ready_execution']
    blocks=set()
    if drift or result['score_breadth']=='INSUFFICIENT' or not ge('dynamic_quality',dynamic_required) or not truth('source_integrity') or risk is None:
        blocks.add('DATA')
    if not ge('execution_quality',execution_required):blocks.add('LIQUIDITY')
    if state['squeeze_blocked']:blocks.add('SQUEEZE')
    breadth_ready=result['score_breadth']=='FULL' or new_exception
    if result['score_breadth']=='REDUCED' and not new_exception:blocks.add('DATA')
    trade=state['trade_state']
    if trade not in TERMINALS and trade!='WATCH':
        # Invalidation wins over expiry; quality loss does not erase old events.
        invalid=truth('invalidate_setup') or state['phase'] not in COUPLING[trade] or result['score_breadth']=='INSUFFICIENT' or (trade in {'SHORT_READY','SHORT_TRIGGERED'} and not breadth_ready) or (trade=='SHORT_TRIGGERED' and bool(blocks))
        terminal='INVALIDATED' if invalid else 'EXPIRED' if state['expires_at'] and now>=state['expires_at'] else None
        if terminal:
            validate_edge(state['trade_state'],terminal)
            state['last_event_id']=emit(terminal,state['chain_id'],state['last_event_id'],'conditions_invalid' if invalid else 'ttl_elapsed')
            state['trade_state']=terminal
    if state['trade_state'] not in TERMINALS:
        phase=state['phase']
        setup=phase in COUPLING['SHORT_SETUP'] and score is not None and score>=t['setup_score'] and strong>=t['strong_families'] and truth('oi_15m_retreat_confirmed') and (lt('perp_cvd',0) or lt('spot_cvd',0) or lt('taker_ratio',t['setup_taker']) or truth('lower_high_candidate'))
        ready=phase in COUPLING['SHORT_READY'] and score is not None and score>=t['ready_score'] and ge('dynamic_quality',t['ready_dynamic']) and ge('execution_quality',t['ready_execution']) and strong>=t['strong_families'] and (truth('lower_high_confirmed') or truth('support_rejection_confirmed')) and (result['score_breadth']=='FULL' or new_exception)
        trigger=phase=='MARKDOWN' and ge('dynamic_quality',t['trigger_dynamic']) and ge('execution_quality',t['trigger_execution']) and lt('oi_15m',0) and (lt('oi_1h',0) or lt('perp_cvd',0) or lt('spot_cvd',0) or lt('taker_ratio',t['trigger_taker'])) and not blocks
        for old_trade,new_trade,condition in [('WATCH','SHORT_SETUP',setup),('SHORT_SETUP','SHORT_READY',ready),('SHORT_READY','SHORT_TRIGGERED',trigger)]:
            if state['trade_state']!=old_trade or not condition:continue
            validate_edge(old_trade,new_trade)
            if new_trade=='SHORT_SETUP':
                state['cycle']+=1
                state['chain_id']=digest([key,'chain',state['cycle']])
                state['expires_at']=stamp(utc(now)+timedelta(seconds=t['ttl_seconds']))
            state['last_event_id']=emit(new_trade,state['chain_id'],state['last_event_id'])
            state['trade_state']=new_trade
    if state['trade_state']=='SHORT_READY' and state['phase']!='MARKDOWN':
        blocks.add('PRICE_CONFIRM')
    active_blocks=[b for b in BLOCK_ORDER if b in blocks]
    result.update(phase=state['phase'],trade_state=state['trade_state'],active_blocks=active_blocks,
        display_gate=active_blocks[0] if active_blocks else 'OPEN',executable=not active_blocks,
        signal_eligible=state['trade_state']=='SHORT_TRIGGERED' and not active_blocks,
        signal_chain_id=state['chain_id'],last_event_id=state['last_event_id'],pump_anchor_time=state['pump_anchor_time'],
        expires_at=state['expires_at'],squeeze_risk=risk,dynamic_quality=val('dynamic_quality'),execution_quality=val('execution_quality'),
        state_version=STATE_VERSION,deleveraging_scope='BINANCE_LOCAL',model_status='PRE_VALIDATION')
    assert_contract(result,events)
    return state,result,events


def assert_contract(result, events):
    trade=result['trade_state'];phase=result['phase']
    if trade in COUPLING and phase not in COUPLING[trade]:raise ValueError('phase/trade coupling violated')
    if result['executable'] != (not result['active_blocks']) or (result['display_gate']=='OPEN') != result['executable']:
        raise ValueError('gate disagrees with authoritative blocks')
    if trade=='SHORT_TRIGGERED' and result['active_blocks']:raise ValueError('trigger is blocked')
    if result['score_breadth']=='INSUFFICIENT' and trade not in {'WATCH',*TERMINALS}:
        raise ValueError('insufficient evidence cannot retain an active trade state')
    terminal=[r for r in events if r['event_type'] in TERMINALS]
    if len(terminal)>1:raise ValueError('two terminal events')
