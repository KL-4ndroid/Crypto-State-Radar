"""Bounded block discovery with contiguous receipts and durable stream cursor."""
from copy import deepcopy
from datetime import datetime, timezone
import os
import re

from .contracts import entity, quantity, hash32, block_evidence
from .rpc import ReadRPC
from ..algorithms.bsc import BSCDiscovery
from ..algorithms.contracts import AlgorithmInput
from ..record_stream import RecordStream, StreamCapacity
from ..v33.contracts import digest

# Reference from the inspected Sniper version. Not an endorsement, allowlist,
# verified ownership check or proof of Agent ID exclusivity.
REGISTRY_REFERENCE = '60b9262456de011f7d15a54cdd8c48baaa8b022f'


class ChainReorganization(RuntimeError):
    pass


def stream_for(journal):
    environment = journal.sql('SELECT environment FROM csr_runtime WHERE id=1').fetchone()[0]
    name = os.getenv('CSR_BSC_STREAM') or f'bsc:{environment}:readonly-v1'
    if not name.startswith(f'bsc:{environment}:'):
        raise ValueError('BSC stream must match the ledger environment')
    return RecordStream(journal, name)


def project_block(projects, evidence, algorithm):
    """Pure shared projection for acquisition and offline evidence replay."""
    pending = deepcopy(projects)
    number, when = evidence['number'], evidence['availability_time']
    for observation in evidence['observations']:
        number=observation.get('block_number',evidence['number'])
        key = entity(observation['address'])
        row = pending.setdefault(key, {'entity_id':key, 'address':observation['address'],
            'chain_id':56, 'first_seen_at':when, 'first_seen_block':number,
            'mint_events_observed':0, 'code_checked':False})
        row.update(last_seen_at=when, last_seen_block=number,
                   last_block_hash=observation.get('block_hash',evidence['hash']), last_tx_hash=observation['tx_hash'])
        row.setdefault('discovery_methods', [])
        if observation['kind'] not in row['discovery_methods']:
            row['discovery_methods'].append(observation['kind'])
        row['mint_events_observed'] += int(observation['kind']=='MINT_TRANSFER')
        row['discovery_methods'].sort()
        if not row.get('algorithm_state'):
            outcome = algorithm.evaluate(AlgorithmInput(key, when, values=row, source_integrity=True))
            row.update(state=outcome.state, reasons=list(outcome.reasons), algorithm_state=outcome.next_state)
    return pending


def project_code(projects, evidence, algorithm):
    pending=deepcopy(projects)
    key,when=evidence['entity_id'],evidence['observed_at']
    row=pending[key]
    row.update(code_checked=True,agent_id_reference=REGISTRY_REFERENCE in evidence['code'].lower(),
        code_asof_block=evidence['block_number'],code_checked_at=when)
    outcome=algorithm.evaluate(AlgorithmInput(key,when,values=row,
        previous_state=row.get('algorithm_state'),source_integrity=True))
    row.update(state=outcome.state,reasons=list(outcome.reasons),algorithm_state=outcome.next_state)
    return pending


def collect(stream, rpc, *, max_blocks=1, confirmations=12, start_block=None, max_code=3):
    if type(max_blocks) is not int or not 1 <= max_blocks <= 3 or type(confirmations) is not int or not 1 <= confirmations <= 200:
        raise ValueError('bounded block count and confirmation delay required')
    if type(max_code) is not int or not 0 <= max_code <= 5:
        raise ValueError('bounded code enrichment required')
    revision, saved = stream.load()
    if saved.get('status') == 'REORG_REVIEW':
        raise ChainReorganization('stream halted; replay/recovery in a new stream required')
    stats = stream.stats()
    if stats['logical_json_characters']+65536 > stream.max_bytes or stats['records']+5 > stream.max_records:
        raise StreamCapacity('insufficient reserved evidence capacity; no RPC called')
    if quantity(rpc.call('eth_chainId', [])) != 56:
        raise ValueError('RPC is not BSC mainnet')
    head = quantity(rpc.call('eth_blockNumber', []))
    eligible = head-confirmations
    if eligible < 1:
        raise ValueError('insufficient confirmed history')
    payload = deepcopy(saved)
    projects = payload.setdefault('projects', {})
    algorithm = BSCDiscovery()
    if payload.get('algorithm') and payload['algorithm'] != algorithm.identity.row():
        raise ValueError('BSC algorithm changed; explicit new stream required')
    payload.update(product='bsc', model_version=algorithm.identity.model_version,
        algorithm=algorithm.identity.row(), execution_enabled=False, status='OBSERVING',
        observed_head=head, confirmation_delay=confirmations,
        coverage='DIRECT_DEPLOYMENT_AND_MINT_TRANSFER_ONLY')
    cursor = payload.get('cursor')
    if cursor and start_block is not None and start_block != cursor['number']+1:
        raise ValueError('existing cursor cannot be reset by start-block')
    start = cursor['number']+1 if cursor else eligible if start_block is None else start_block
    if type(start) is not int or start < 1 or start > eligible+1:
        raise ValueError('start-block outside confirmed interval')
    last_number = cursor['number'] if cursor else start-1
    anchor = rpc.call('eth_getBlockByNumber', [hex(last_number), False])
    if quantity(anchor['number']) != last_number:
        raise ValueError('anchor block number mismatch')
    parent = hash32(anchor['hash'])
    if cursor and cursor['hash'] != parent:
        incident = {'kind':'REORG', 'number':last_number, 'expected':cursor['hash'], 'actual':parent,
                    'observed_at':datetime.now(timezone.utc).isoformat()}
        payload.update(status='REORG_REVIEW', last_error='ANCHOR_HASH_CHANGED', last_attempt_at=incident['observed_at'])
        stream.commit(revision, payload, [('incident:'+digest(incident), incident)])
        raise ChainReorganization('anchor hash changed; cursor retained')
    payload.setdefault('coverage_start_block', start)
    payload.setdefault('anchor', {'number':last_number, 'hash':parent})
    completed = 0
    for number in range(start, min(eligible+1, start+max_blocks)):
        block = rpc.call('eth_getBlockByNumber', [hex(number), False])
        if quantity(block['number']) != number or hash32(block['parentHash']) != parent:
            raise ChainReorganization('non-contiguous block parent; cursor retained')
        receipts = rpc.call('eth_getBlockReceipts', [hex(number)])
        now = datetime.now(timezone.utc)
        evidence = block_evidence(block, receipts, now)
        pending = project_block(projects,evidence,algorithm)
        if len(pending) > 500:
            raise StreamCapacity('project retention ceiling reached; no block skipped')
        payload.update(projects=pending, cursor={'number':number,'hash':evidence['hash']},
            last_scan={'processed_at':now.isoformat(),'scanned':1,'errors':0,'scanned_through':number},
            lag_blocks=eligible-number, last_error=None)
        output = stream.commit(revision, payload, [('block:'+evidence['hash'], evidence)])
        revision, payload, projects = output['revision'], deepcopy(output), deepcopy(pending)
        parent, completed = evidence['hash'], completed+1
    # Enrichment failure leaves explicit pending work; it never marks code safe.
    for key in sorted((key for key,row in projects.items() if not row['code_checked']),
                      key=lambda key:(projects[key]['first_seen_block'],key))[:max_code]:
        row = projects[key]
        try:
            code = rpc.call('eth_getCode', [row['address'], hex(payload['cursor']['number'])])
            if not isinstance(code,str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})+',code):
                raise ValueError('contract bytecode unavailable')
            current=rpc.call('eth_getBlockByNumber',[hex(payload['cursor']['number']),False])
            if hash32(current['hash'])!=payload['cursor']['hash']:
                raise ChainReorganization('code block changed during enrichment')
        except ChainReorganization:
            incident={'kind':'REORG','observed_at':datetime.now(timezone.utc).isoformat(),
                      'number':payload['cursor']['number'],'reason':'CODE_BLOCK_CHANGED'}
            payload.update(status='REORG_REVIEW',last_error='CODE_BLOCK_CHANGED')
            stream.commit(revision,payload,[('incident:'+digest(incident),incident)])
            raise
        except (ValueError, RuntimeError, TimeoutError):
            break
        now = datetime.now(timezone.utc)
        evidence = {'kind':'CODE', 'entity_id':key, 'block_hash':payload['cursor']['hash'],
            'block_number':payload['cursor']['number'], 'observed_at':now.isoformat(), 'code':code.lower()}
        projects=project_code(projects,evidence,algorithm)
        payload.update(projects=projects)
        output = stream.commit(revision, payload, [('code:'+digest(evidence),evidence)])
        revision, payload = output['revision'], deepcopy(output)
    # Fresh liveness metadata even when caught up; this does not refresh source times.
    now = datetime.now(timezone.utc).isoformat()
    payload.update(last_attempt_at=now, lag_blocks=max(0,eligible-payload.get('cursor',payload['anchor'])['number']))
    output = stream.commit(revision, payload, [('receipt:'+digest([now,revision]),
        {'kind':'COLLECTION','observed_at':now,'blocks':completed,'requests':rpc.budget.report()} )])
    return output


def publish_current(journal):
    from ..state_store import state_store_from_env
    stream=stream_for(journal)
    revision,payload=stream.load()
    if not revision or payload.get('product')!='bsc':raise ValueError('no BSC snapshot')
    key=os.getenv('CSR_STATE_KEY','')
    if not key.startswith(f'crypto-state-radar:bsc:{stream.environment}:'):
        raise ValueError('BSC publication requires an isolated environment key')
    gateway=os.getenv('CSR_BSC_PUBLISH_URL')
    if gateway:
        import requests
        from urllib.parse import urlparse
        target=urlparse(gateway)
        allowed=target.hostname=='crypto-state-radar.vercel.app' if stream.environment=='production' else bool(target.hostname and target.hostname.startswith('crypto-state-radar-') and target.hostname.endswith('-masons-projects-1db534c5.vercel.app'))
        if target.scheme!='https' or not allowed or target.path!='/api/bsc' or target.query or target.fragment or target.username:
            raise ValueError('invalid publication gateway')
        headers={'Authorization':'Bearer '+os.environ['CSR_WORKER_TOKEN']}
        if os.getenv('CSR_BSC_PREVIEW_OIDC') and stream.environment=='preview':
            headers['x-vercel-trusted-oidc-idp-token']=os.environ['CSR_BSC_PREVIEW_OIDC']
        response=requests.post(gateway,params={'operation':'publish'},headers=headers,timeout=15,allow_redirects=False)
        if response.status_code!=200:raise RuntimeError('publication gateway unavailable')
        result=response.json()
        if result.get('published') is not True or result.get('revision',0)<revision:raise RuntimeError('publication not acknowledged')
        return True
    return state_store_from_env(load=False).publish_snapshot(payload)


def run(journal, *, rpc=None, max_blocks=1, start_block=None, publish=False):
    stream = stream_for(journal)
    rpc = rpc or ReadRPC([u.strip() for u in os.getenv('CSR_BSC_RPC_URLS','https://bsc-rpc.publicnode.com').split(',') if u.strip()])
    if os.getenv('CSR_BSC_DISCOVERY_MODE')=='mint_logs':
        from .log_scanner import collect_logs
        output=collect_logs(stream,rpc,start_block=start_block)
    else:
        output = collect(stream,rpc,max_blocks=max_blocks,start_block=start_block)
    published = False
    if publish:
        published=publish_current(journal)
    return {'status':output['status'],'revision':output['revision'],'projects':len(output['projects']),
        'pending_code':sum(not row['code_checked'] for row in output['projects'].values()),
        'cursor':output.get('cursor'),'lag_blocks':output['lag_blocks'],'published':published,
        'execution_enabled':False,'request_metrics':rpc.budget.report()}
