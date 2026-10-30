"""Bounded Binance responses, normalization and explicit page-completeness QA."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
import json
import os
import re
import time

import requests
from .causal import epoch_time
from .contracts import digest,stamp
from .sources import SOURCES

INTERVAL_MS=300000
STATISTICS={'oi','taker','top_account','top_position','global_accounts'}
TRADES={'spot_aggtrades','perp_aggtrades'}
SUPPORTED=STATISTICS|TRADES|{'funding_settlement','premium_mark_index'}


class AcquisitionError(RuntimeError):pass
class DataGap(AcquisitionError):pass
class AuthRequired(AcquisitionError):pass
class NoClosedData(AcquisitionError):pass
class ClockSkew(AcquisitionError):pass
class ProviderError(AcquisitionError):
    def __init__(self,status,retry_after=None):
        super().__init__('provider request failed');self.status=status;self.retry_after=retry_after


def decimal(value,nonnegative=False):
    if isinstance(value,bool):raise ValueError('invalid numerical response')
    try:result=Decimal(str(value))
    except InvalidOperation:raise ValueError('invalid numerical response') from None
    if not result.is_finite() or (nonnegative and result<0):raise ValueError('invalid numerical response')
    return result


def integer(value):
    if isinstance(value,bool) or not isinstance(value,int) or value<0:raise ValueError('invalid integer response')
    return value


class BinanceAcquisitionHTTP:
    """Separate raw-response adapter; shares the existing RequestBudget class.

    Redirects are disabled, including for authenticated requests. Only fixed
    official data endpoints can receive the optional read-only data API key.
    """
    def __init__(self,budget,max_body_bytes=4*1024*1024):
        self.budget=budget;self.max_body_bytes=max_body_bytes
        self.measurements={'known_request_weight':0,'unknown_weight_requests':0,'last_server_used_weight_1m':None}

    def fetch(self,source,params):
        if source not in SUPPORTED:raise ValueError('unsupported source')
        contract=SOURCES[source];headers={'User-Agent':'Crypto-State-Radar/V3.3-acquisition'}
        if contract.auth_requirement=='X-MBX-APIKEY':
            key=os.getenv('CSR_BINANCE_DATA_API_KEY')
            if not key:raise AuthRequired('read-only data API key required')
            headers['X-MBX-APIKEY']=key
        endpoint=contract.endpoint
        url=endpoint if source=='spot_aggtrades' else 'https://fapi.binance.com'+endpoint
        timeout=self.budget.begin();started=time.monotonic();failed=True
        weight=0 if source in STATISTICS else {'spot_aggtrades':4,'perp_aggtrades':20}.get(source)
        if weight is None:self.measurements['unknown_weight_requests']+=1
        else:self.measurements['known_request_weight']+=weight
        try:
            with requests.get(url,params=params,headers=headers,timeout=(min(3,timeout),timeout),stream=True,allow_redirects=False) as response:
                used=response.headers.get('X-MBX-USED-WEIGHT-1M','')
                if used.isdigit():self.measurements['last_server_used_weight_1m']=int(used)
                if response.status_code!=200:
                    retry=response.headers.get('Retry-After','')
                    raise ProviderError(response.status_code,min(86400,int(retry)) if retry.isdigit() else None)
                chunks=[];size=0
                for chunk in response.iter_content(65536):
                    if time.monotonic()>self.budget.deadline:raise TimeoutError('acquisition deadline exceeded')
                    size+=len(chunk)
                    if size>self.max_body_bytes:raise ValueError('provider body exceeds budget')
                    chunks.append(chunk)
                body=b''.join(chunks)
            payload=json.loads(body)
            if not isinstance(payload,(dict,list)):raise ValueError('unexpected provider response')
            failed=False
            return payload,body,datetime.now(timezone.utc)
        finally:self.budget.finish(time.monotonic()-started,failed)


def request_plan(source,symbol,cursor,now,limit=500,close_delay_seconds=5):
    if source not in SUPPORTED or not re.fullmatch(r'[A-Z0-9]{2,30}',symbol):raise ValueError('unsupported source or symbol')
    if type(limit) is not int or not 1<=limit<=500:raise ValueError('acquisition page limit must be 1..500')
    cutoff=(int(now.timestamp()*1000)-close_delay_seconds*1000)//INTERVAL_MS*INTERVAL_MS
    params={'symbol':symbol}
    if source=='premium_mark_index':return params,{'cutoff_ms':cutoff,'kind':'SNAPSHOT'}
    if source in TRADES:
        start=cursor['window_start_ms'];end=start+INTERVAL_MS
        if end>cutoff:raise NoClosedData('trade interval is not closed')
        params['limit']=limit
        if cursor['next_id'] is None:params.update(startTime=start,endTime=end-1)
        else:params['fromId']=cursor['next_id']
        return params,{'cutoff_ms':end,'start_ms':start,'limit':limit,'kind':'TRADE_WINDOW'}
    start=cursor['next_time_ms']
    if source in STATISTICS:
        end=cutoff-INTERVAL_MS if source=='taker' else cutoff
        if start>end:raise NoClosedData('statistics interval is not due')
        # Live Taker responses can contain a one-interval leading overlap and
        # filter by interval end. Request through cutoff, then retain only the
        # explicitly closed row-time range in the normalizer.
        params.update(period='5m',startTime=start,endTime=cutoff if source=='taker' else end,limit=min(limit,500))
    else:
        # Overlap protects late funding publication without duplicating items.
        start=max(cursor.get('origin_ms',start),start-3600000)
        end=int(now.timestamp()*1000)
        params.update(startTime=start,endTime=end,limit=limit)
    return params,{'cutoff_ms':cutoff,'start_ms':start,'end_ms':end,'limit':params['limit'],'kind':'STATISTICS' if source in STATISTICS else 'FUNDING'}


def normalize_statistics(source,symbol,payload,plan,cursor):
    if not isinstance(payload,list) or len(payload)>plan['limit']:raise ValueError('invalid statistics page')
    rows=[];previous=None;next_cursor=deepcopy(cursor);seen=set()
    for item in payload:
        # Taker rows omit symbol; the fixed single-symbol request supplies it.
        if item.get('symbol',symbol)!=symbol:raise ValueError('wrong response symbol')
        when=integer(item['fundingTime'] if source=='funding_settlement' else item['timestamp'])
        minimum=plan['start_ms']-INTERVAL_MS if source=='taker' else plan['start_ms']
        maximum=plan['cutoff_ms'] if source=='taker' else plan['end_ms']
        if when<minimum or when>maximum:raise ValueError('response timestamp outside requested bounds')
        event_key=(when,item.get('rateType','Regular')) if source=='funding_settlement' else when
        if event_key in seen or (previous is not None and (when<previous or (when==previous and source!='funding_settlement'))):
            raise ValueError('unordered or duplicate response timestamp')
        seen.add(event_key)
        effective=when+INTERVAL_MS if source=='taker' else when
        if source=='taker' and (when<plan['start_ms'] or effective>plan['cutoff_ms']):continue
        if source in STATISTICS:
            if when%INTERVAL_MS or (previous is not None and when-previous!=INTERVAL_MS):raise DataGap('statistics gap')
            if previous is None and when!=plan['start_ms']:raise DataGap('statistics start is missing')
        previous=when
        if source in STATISTICS and effective>plan['cutoff_ms']:raise ValueError('unclosed statistics interval')
        values={'raw_timestamp':when,'raw_item_hash':digest(item)}
        if source=='oi':
            values.update(quantity=str(decimal(item['sumOpenInterest'],True)),notional=str(decimal(item['sumOpenInterestValue'],True)))
        elif source=='taker':
            buy=decimal(item['buyVol'],True);sell=decimal(item['sellVol'],True)
            values.update(buy_volume=str(buy),sell_volume=str(sell),ratio=str(buy/sell) if sell else None)
        elif source in {'top_account','top_position','global_accounts'}:
            long=decimal(item['longAccount'],True);short=decimal(item['shortAccount'],True)
            if long>1 or short>1:raise ValueError('invalid account or position fraction')
            values.update(long_fraction=str(long),short_fraction=str(short),ratio=str(decimal(item['longShortRatio'],True)))
        elif source=='funding_settlement':
            values.update(rate=str(decimal(item['fundingRate'])),rate_type=item.get('rateType','Regular'))
        rows.append({'key':str(when)+(':'+values['rate_type'] if source=='funding_settlement' else ''),
                     'effective_time':epoch_time(effective,'ms'),'values':values})
    if source in STATISTICS:
        if not rows:raise NoClosedData('empty statistics response is not zero')
        next_cursor['next_time_ms']=previous+INTERVAL_MS
        complete=previous>=plan['end_ms']
    else:
        if len(payload)==plan['limit']:raise DataGap('funding boundary requires a larger explicit window policy')
        next_cursor['next_time_ms']=(previous+1 if len(payload)==plan['limit'] else plan['end_ms']+1)
        complete=len(payload)<plan['limit']
    return rows,next_cursor,complete


def normalize_snapshot(symbol,payload,observed):
    if not isinstance(payload,dict) or payload.get('symbol')!=symbol:raise ValueError('wrong snapshot symbol')
    when=integer(payload['time'])
    if epoch_time(when,'ms')>stamp(observed):raise ClockSkew('future snapshot')
    values={'raw_timestamp':when,'raw_item_hash':digest(payload),'mark_price':str(decimal(payload['markPrice'],True)),
            'index_price':str(decimal(payload['indexPrice'],True)),
            'funding_quote':str(decimal(payload['lastFundingRate'])),
            'next_funding_ms':integer(payload['nextFundingTime'])}
    return [{'key':str(when),'effective_time':epoch_time(when,'ms'),'values':values}]


def normalize_trades(source,payload,plan,cursor,archive_sha,max_pages=1000):
    if not isinstance(payload,list) or len(payload)>plan['limit']:raise ValueError('invalid trades page')
    out=deepcopy(cursor);accepted=[];expected=cursor['next_id'];previous_time=None;future=False
    delta=decimal(out['quote_delta']);buy=decimal(out['buy_quote']);sell=decimal(out['sell_quote'])
    for trade in payload:
        ident=integer(trade['a']);when=integer(trade['T'])
        if expected is not None and ident!=expected:raise DataGap('aggregate trade ID gap')
        if previous_time is not None and when<previous_time:raise ValueError('unordered trade timestamps')
        expected=ident+1;previous_time=when
        if when<plan['start_ms']:raise DataGap('late trade requires explicit repair')
        if when>=plan['cutoff_ms']:
            if cursor['next_id'] is None:raise ValueError('trade response exceeds explicit time bounds')
            future=True;break
        if type(trade['m']) is not bool:raise ValueError('invalid maker side')
        price=decimal(trade['p'],True);quantity=decimal(trade['q'],True)
        if price<=0 or quantity<=0:raise ValueError('invalid trade price or quantity')
        quote=price*quantity
        if trade['m']:delta-=quote;sell+=quote
        else:delta+=quote;buy+=quote
        accepted.append(trade);out['next_id']=ident+1
    # A cold empty response cannot establish the first known trade ID.
    if not accepted and not future and cursor['next_id'] is None:raise NoClosedData('empty bootstrap trades are not CVD zero')
    out.update(quote_delta=str(delta),buy_quote=str(buy),sell_quote=str(sell),trade_count=out['trade_count']+len(accepted))
    out['page_ids'].append(archive_sha)
    if len(out['page_ids'])>max_pages:raise ValueError('trade window page budget exhausted')
    complete=future or len(payload)<plan['limit']
    rows=[]
    if complete:
        if out['next_id'] is None:
            # A future boundary trade is a valid anchor for an empty closed window.
            out['next_id']=payload[0]['a']
        values={'raw_timestamp':plan['cutoff_ms'],'window_start_ms':plan['start_ms'],'window_end_ms':plan['cutoff_ms'],
                'quote_delta':str(delta),'buy_quote':str(buy),'sell_quote':str(sell),'trade_count':out['trade_count'],
                'page_hashes':list(out['page_ids']),'coverage':'REST_OBSERVED_CONTIGUOUS','quantity_field':'q',
                'rpi_scope':'INCLUDED_NOT_SEPARATELY_IDENTIFIABLE' if source=='perp_aggtrades' else 'NOT_APPLICABLE'}
        rows=[{'source':source+'_5m','key':str(plan['start_ms']),
               'effective_time':epoch_time(plan['cutoff_ms'],'ms'),'values':values}]
        out.update(window_start_ms=plan['cutoff_ms'],quote_delta='0',buy_quote='0',sell_quote='0',trade_count=0,page_ids=[])
    return rows,out,complete
