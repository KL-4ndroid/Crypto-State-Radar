"""Causal measurement primitives; never produce Family scores or trade predicates."""
from datetime import timedelta
from .contracts import Feature,stamp
from .acquisition import AcquisitionQueue
from .acquisition_sources import decimal
from ..retry_policy import utc

FEATURE_VERSION='v33-measurements-1'


def derive_measurements(store,symbol,when,archive):
    when=stamp(when);queue=AcquisitionQueue(store);written=[];verified=set()
    def emit(name,value,records,reason=None):
        for record in records:
            for key in [record['payload']['archive']['sha256'],*record['payload']['values'].get('page_hashes',[])]:
                if key not in verified:archive.read(key);verified.add(key)
        ids=tuple(sorted({r['record_id'] for r in records}))
        effective=max(r['effective_time'] for r in records)
        if any(r['availability_time']>when for r in records):raise ValueError('future acquisition')
        previous=store.asof(symbol,name,FEATURE_VERSION,when)
        if previous and previous['asof_time']==effective and previous['source_ids']==list(ids) and previous['value']==value and previous['missing_reason']==reason:
            written.append(previous['feature_id']);return
        written.append(store.put_feature(Feature(name,FEATURE_VERSION,symbol,effective,effective,when,value,ids,reason)))
    for source in ('oi','taker','top_account','top_position','global_accounts','premium_mark_index','funding_settlement','spot_aggtrades_5m','perp_aggtrades_5m'):
        latest=queue.item_at(source,symbol,when,when)
        if latest is None:continue
        values=latest['payload']['values']
        if source=='oi':
            emit('oi_quantity',float(decimal(values['quantity'])),[latest])
            emit('oi_notional',float(decimal(values['notional'])),[latest])
            for minutes,label in ((15,'15m'),(60,'1h'),(240,'4h'),(1440,'24h')):
                old=queue.item_at(source,symbol,utc(latest['effective_time'])-timedelta(minutes=minutes),when,exact=True)
                for field,name in (('quantity','oi_'+label),('notional','oi_notional_'+label)):
                    baseline=decimal(old['payload']['values'][field]) if old else None
                    value=float((decimal(values[field])/baseline-1)*100) if baseline else None
                    emit(name,value,[latest,old] if old else [latest],None if value is not None else 'missing_or_zero_exact_baseline')
        elif source=='taker':
            ratio=values['ratio'];emit('taker_ratio',float(decimal(ratio)) if ratio is not None else None,[latest],None if ratio is not None else 'zero_sell_volume')
        elif source in {'top_account','top_position','global_accounts'}:
            name={'top_account':'top_account_long_fraction','top_position':'top_position_long_fraction','global_accounts':'global_account_long_fraction'}[source]
            emit(name,float(decimal(values['long_fraction'])),[latest])
        elif source=='funding_settlement':emit('funding_rate_settled',float(decimal(values['rate'])),[latest])
        elif source=='premium_mark_index':
            emit('mark_price',float(decimal(values['mark_price'])),[latest]);emit('index_price',float(decimal(values['index_price'])),[latest])
            emit('funding_rate_quote',float(decimal(values['funding_quote'])),[latest])
            index=decimal(values['index_price']);basis=float(decimal(values['mark_price'])/index-1) if index else None
            emit('mark_index_basis_fraction',basis,[latest],None if basis is not None else 'zero_index')
        elif source.endswith('_aggtrades_5m'):
            name='spot_cvd_5m_quote' if source.startswith('spot') else 'perp_cvd_5m_quote'
            emit(name,float(decimal(values['quote_delta'])),[latest])
    return {'symbol':symbol,'feature_ids':written,'feature_version':FEATURE_VERSION,'model_evaluated':False}
