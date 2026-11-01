"""Efficient contiguous Mint-log ranges; explicitly not deployment coverage."""
from copy import deepcopy
from datetime import datetime,timezone
import re
import os
from .contracts import quantity,hash32,address,TRANSFER,ZERO
from .scanner import project_block,project_code,ChainReorganization
from ..algorithms.bsc import BSCDiscovery
from ..record_stream import StreamCapacity
from ..v33.contracts import digest


def collect_logs(stream,rpc,*,start_block=None,max_range=128,confirmations=12):
    addresses=sorted({address(a.strip()) for a in os.getenv('CSR_BSC_WATCH_ADDRESSES','').split(',') if a.strip()})
    if not 1<=len(addresses)<=32:raise ValueError('one to 32 explicit watch addresses required')
    if type(max_range) is not int or not 1<=max_range<=256 or type(confirmations) is not int or not 1<=confirmations<=200:
        raise ValueError('bounded log range and confirmation delay required')
    revision,saved=stream.load()
    if saved.get('status')=='REORG_REVIEW':raise ChainReorganization('recovery stream required')
    if saved.get('coverage') not in {None,'WATCHLIST_MINT_RPC_RANGE'}:
        raise ValueError('range discovery requires a separate stream')
    if saved.get('watch_addresses') is not None and saved['watch_addresses']!=addresses:
        raise ValueError('watch universe changed; new stream/replay required')
    stats=stream.stats()
    if stats['logical_json_characters']+65536>stream.max_bytes or stats['records']+5>stream.max_records:
        raise StreamCapacity('evidence capacity exhausted before RPC')
    if quantity(rpc.call('eth_chainId',[]))!=56:raise ValueError('not BSC mainnet')
    head=quantity(rpc.call('eth_blockNumber',[]));eligible=head-confirmations
    if eligible<1:raise ValueError('confirmed history unavailable')
    cursor=saved.get('cursor')
    start=cursor['number']+1 if cursor else max(1,eligible-31) if start_block is None else start_block
    if cursor and start_block is not None and start_block!=start:raise ValueError('cursor reset forbidden')
    if type(start) is not int or not 1<=start<=eligible+1:raise ValueError('invalid start block')
    anchor=rpc.call('eth_getBlockByNumber',[hex(start-1),False])
    if quantity(anchor['number'])!=start-1:raise ValueError('anchor number mismatch')
    anchor_hash=hash32(anchor['hash'])
    payload=deepcopy(saved);algorithm=BSCDiscovery()
    if payload.get('algorithm') and payload['algorithm']!=algorithm.identity.row():raise ValueError('algorithm changed; new stream required')
    now=datetime.now(timezone.utc)
    payload.update(product='bsc',execution_enabled=False,coverage='WATCHLIST_MINT_RPC_RANGE',watch_addresses=addresses,
        model_version=algorithm.identity.model_version,algorithm=algorithm.identity.row(),
        status='OBSERVING',observed_head=head,confirmation_delay=confirmations)
    if cursor and cursor['hash']!=anchor_hash:
        payload.update(status='REORG_REVIEW',last_error='ANCHOR_HASH_CHANGED',last_attempt_at=now.isoformat())
        incident={'kind':'REORG','number':cursor['number'],'old':cursor['hash'],'new':anchor_hash}
        stream.commit(revision,payload,[('incident:'+digest(incident),incident)])
        raise ChainReorganization('anchor changed; no range advanced')
    payload.setdefault('projects',{});payload.setdefault('coverage_start_block',start)
    records=[]
    if start<=eligible:
        end=min(eligible,start+max_range-1)
        header=rpc.call('eth_getBlockByNumber',[hex(end),False])
        if quantity(header['number'])!=end:raise ValueError('end block mismatch')
        end_hash=hash32(header['hash'])
        logs=rpc.call('eth_getLogs',[{'address':addresses,'fromBlock':hex(start),'toBlock':hex(end),'topics':[TRANSFER,ZERO]}])
        if not isinstance(logs,list) or len(logs)>1000:raise ValueError('unbounded or missing log result')
        seen=set();observations=[]
        for log in logs:
            number=quantity(log['blockNumber']);tx=hash32(log['transactionHash']);index=quantity(log['logIndex']);bh=hash32(log['blockHash'])
            topics=log.get('topics',[])
            if address(log['address']) not in addresses or not start<=number<=end or log.get('removed') is not False or len(topics)<3 or hash32(topics[0])!=TRANSFER or hash32(topics[1])!=ZERO:
                raise ValueError('invalid mint log')
            if number==end and bh!=end_hash:raise ChainReorganization('log belongs to another end block')
            key=(bh,tx,index)
            if key in seen:raise ValueError('duplicate mint log')
            seen.add(key)
            observations.append({'kind':'MINT_TRANSFER','address':address(log['address']),
                'recipient':address('0x'+hash32(topics[2])[-40:]),'tx_hash':tx,'log_index':index,
                'block_number':number,'block_hash':bh})
        # Stable end anchor checked after the range response. Completeness is
        # provider-declared; this is not a receipt/Merkle proof of all events.
        final=rpc.call('eth_getBlockByNumber',[hex(end),False])
        if hash32(final['hash'])!=end_hash:raise ChainReorganization('range changed during collection')
        now=datetime.now(timezone.utc)
        source_time=datetime.fromtimestamp(quantity(header['timestamp']),timezone.utc)
        if source_time>now:raise ValueError('future block time')
        evidence={'kind':'MINT_RANGE','chain_id':56,'data_version':'bsc-evidence-1',
            'range_start':start,'number':end,'hash':end_hash,'parent_hash':anchor_hash,
            'source_time':source_time.isoformat(),'availability_time':now.isoformat(),
            'observations':sorted(observations,key=lambda o:(o['block_number'],o['log_index'])),
            'watch_addresses':addresses,'coverage':'WATCHLIST_MINT_RPC_RANGE','completeness':'RPC_DECLARED_NOT_CRYPTOGRAPHICALLY_VERIFIED'}
        projects=project_block(payload['projects'],evidence,algorithm)
        if len(projects)>500:raise StreamCapacity('project ceiling; no range skipped')
        payload.update(projects=projects,cursor={'number':end,'hash':end_hash},last_error=None,
            last_scan={'processed_at':now.isoformat(),'scanned':end-start+1,'errors':0,'scanned_through':end})
        result=stream.commit(revision,payload,[('range:'+str(start)+':'+end_hash,evidence)])
        revision,payload=result['revision'],deepcopy(result)
    for key in sorted(k for k,r in payload['projects'].items() if not r['code_checked'])[:2]:
        try:
            code=rpc.call('eth_getCode',[payload['projects'][key]['address'],hex(payload['cursor']['number'])])
            if not isinstance(code,str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})+',code):raise ValueError('code unavailable')
            header=rpc.call('eth_getBlockByNumber',[hex(payload['cursor']['number']),False])
            if hash32(header['hash'])!=payload['cursor']['hash']:raise ChainReorganization('code anchor changed')
        except ChainReorganization:raise
        except (ValueError,RuntimeError,TimeoutError):break
        now=datetime.now(timezone.utc)
        evidence={'kind':'CODE','entity_id':key,'block_hash':payload['cursor']['hash'],
            'block_number':payload['cursor']['number'],'observed_at':now.isoformat(),'code':code.lower()}
        payload['projects']=project_code(payload['projects'],evidence,algorithm)
        result=stream.commit(revision,payload,[('code:'+digest(evidence),evidence)])
        revision,payload=result['revision'],deepcopy(result)
    now=datetime.now(timezone.utc).isoformat()
    payload.update(last_attempt_at=now,lag_blocks=max(0,eligible-payload.get('cursor',{'number':start-1})['number']))
    return stream.commit(revision,payload,[('receipt:'+digest([now,revision]),
        {'kind':'COLLECTION','observed_at':now,'requests':rpc.budget.report()})])
