"""One bounded acquisition cycle; no scanner, score, live publication or cron."""
from datetime import datetime,timedelta,timezone
import re
import time

from .acquisition import AcquisitionQueue,StaleAcquisition
from .acquisition_sources import (SUPPORTED,STATISTICS,TRADES,AcquisitionError,AuthRequired,
    NoClosedData,ProviderError,BinanceAcquisitionHTTP,request_plan,normalize_statistics,
    normalize_snapshot,normalize_trades)
from .contracts import stamp
from .sources import SOURCES
from ..request_budget import RequestBudget
from ..retry_policy import utc


class AcquisitionWorker:
    def __init__(self,store,archive,transport=None,max_requests=12,seconds=40,page_limit=500,clock=None,max_clock_wait_seconds=1):
        if type(max_requests) is not int or not 1<=max_requests<=50 or not 1<=seconds<=40:
            raise ValueError('bounded acquisition limits required')
        self.queue=AcquisitionQueue(store);self.archive=archive
        self.started=time.monotonic()
        self.budget=RequestBudget(seconds=seconds,max_requests=max_requests)
        self.transport=transport or BinanceAcquisitionHTTP(self.budget)
        self.clock=clock or (lambda:datetime.now(timezone.utc));self.page_limit=page_limit
        if not 0<=max_clock_wait_seconds<=1:raise ValueError('clock wait must be bounded to one second')
        self.max_clock_wait_seconds=max_clock_wait_seconds

    def cycle(self,symbols,sources,start):
        if not symbols or len(set(symbols))>5 or not all(isinstance(s,str) and re.fullmatch(r'[A-Z0-9]{2,30}',s) for s in symbols) or not sources or not set(sources)<=SUPPORTED:
            raise ValueError('pilot supports 1..5 explicit symbols and known sources')
        now=utc(self.clock());start=utc(start);start_ms=int(start.timestamp()*1000)
        if start_ms%300000 or start>now:raise ValueError('bootstrap start must be a past five-minute boundary')
        results=[]
        storage=self.archive.stats()
        if storage.get('objects',0)>=storage.get('max_objects',float('inf')) or storage.get('logical_payload_bytes',0)>=storage.get('max_total_bytes',float('inf')):
            return self.report(results,storage_blocked=True)
        for symbol in sorted(set(symbols)):
            order={'premium_mark_index':0,'oi':1,'taker':2,'global_accounts':3,'top_account':4,'top_position':5,'funding_settlement':6,'spot_aggtrades':7,'perp_aggtrades':8}
            for source in sorted(set(sources),key=lambda s:order[s]):
                if time.monotonic()>=self.budget.deadline or self.budget.calls>=self.budget.max_requests:
                    results.append({'source':source,'symbol':symbol,'status':'BUDGET_DEFERRED'});continue
                now=utc(self.clock())
                if self.queue.status(source,symbol) is None and (now-start).total_seconds()>86400:
                    raise ValueError('new streams require bootstrap within 24h; existing streams resume their cursor')
                self.queue.ensure(source,symbol,start_ms,now)
                claim=self.queue.claim(source,symbol,now)
                if claim is None:
                    results.append({'source':source,'symbol':symbol,'status':'NOT_DUE'});continue
                archive=None
                try:
                    params,plan=request_plan(source,symbol,claim['cursor'],now,self.page_limit)
                    payload,body,observed=self.transport.fetch(source,params)
                    observed=utc(observed)
                    received=stamp(observed)
                    archive=self.archive.put(body)
                    if source in TRADES:
                        items,cursor,complete=normalize_trades(source,payload,plan,claim['cursor'],archive['sha256'])
                        if complete:
                            for item in items:
                                for key in item['values']['page_hashes']:self.archive.read(key)
                    elif source=='premium_mark_index':
                        if isinstance(payload,dict) and type(payload.get('time')) is int:
                            lead=payload['time']/1000-self.clock().timestamp()
                            if 0<lead<=self.max_clock_wait_seconds and time.monotonic()+lead+0.01<self.budget.deadline:
                                # Withhold use until the source clock is no longer
                                # ahead; preserve original receipt time separately.
                                time.sleep(lead+0.01)
                            observed=max(observed,utc(self.clock()))
                        items=normalize_snapshot(symbol,payload,observed);cursor=claim['cursor'];complete=True
                    else:
                        items,cursor,complete=normalize_statistics(source,symbol,payload,plan,claim['cursor'])
                    next_due=observed+timedelta(seconds=300 if complete else 60)
                    if source=='funding_settlement':
                        premium=self.queue.item_at('premium_mark_index',symbol,observed,observed)
                        next_funding=(premium or {}).get('payload',{}).get('values',{}).get('next_funding_ms')
                        due=datetime.fromtimestamp(next_funding/1000,timezone.utc)+timedelta(seconds=60) if next_funding else observed+timedelta(hours=1)
                        next_due=max(observed+timedelta(minutes=5),due) if complete else next_due
                    receipt=self.queue.finish(claim,items,cursor,
                        {'archive':archive,'request':params,'plan':plan,'coverage':'COMPLETE_AS_OBSERVED' if complete else 'PARTIAL',
                         'received_at':received,'normalization_version':'binance-acquisition-1','source_contract_version':SOURCES[source].contract_version,
                         'received_rows':len(payload) if isinstance(payload,list) else 1},observed,next_due,completed_at=self.clock())
                    results.append({'source':source,'symbol':symbol,'status':'COMPLETE' if complete else 'PARTIAL',
                                    'new_records':receipt['new_records'],'revision':receipt['revision'],'archive_bytes':archive['compressed_bytes']})
                except StaleAcquisition:
                    results.append({'source':source,'symbol':symbol,'status':'LEASE_SUPERSEDED'})
                except Exception as exc:
                    now=utc(self.clock())
                    delay=3600 if isinstance(exc,AuthRequired) else 300 if isinstance(exc,NoClosedData) else None
                    if isinstance(exc,ProviderError):
                        delay=max(60,exc.retry_after or (86400 if exc.status in {400,401,403,418,451} else 60))
                    try:self.queue.fail(claim,now,type(exc).__name__,delay,archive)
                    except StaleAcquisition:pass
                    results.append({'source':source,'symbol':symbol,'status':'WAITING_FOR_CLOSED_DATA' if isinstance(exc,NoClosedData) else 'UNAVAILABLE','error_type':type(exc).__name__})
                    if isinstance(exc,OSError):
                        return self.report(results,storage_blocked=True)
                    if isinstance(exc,ProviderError) and exc.status in {418,429,451}:
                        return self.report(results,provider_blocked=True)
        return self.report(results)

    def report(self,results,provider_blocked=False,storage_blocked=False):
        return {'namespace':'SHADOW','run_id':self.queue.store.run_id,'results':results,
                'request_metrics':self.budget.report(),'provider_blocked':provider_blocked,'storage_blocked':storage_blocked,
                'request_weight':getattr(self.transport,'measurements',{'coverage':'NOT_MEASURED_BY_FIXTURE'}),
                'duration_seconds':round(time.monotonic()-self.started,3),'archive_storage':self.archive.stats(),
                'streams':self.queue.summary(),
                'coverage_scope':'EXPLICIT_PILOT_SYMBOLS_ONLY','model_evaluated':False,'snapshot_published':False}
