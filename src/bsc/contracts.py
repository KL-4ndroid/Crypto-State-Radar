"""Chain identity and compact evidence contracts, independent of token names."""
import re
from datetime import datetime, timezone

CHAIN_ID = 56
# ERC20/721 Transfer event signature. Keep the full 32-byte topic.
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
ZERO = '0x' + '0'*64


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', value):
        raise ValueError('invalid chain address')
    return value.lower()


def hash32(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', value):
        raise ValueError('invalid chain hash')
    return value.lower()


def quantity(value):
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]+', value):
        raise ValueError('invalid RPC quantity')
    return int(value, 16)


def entity(value):
    return f'eip155:{CHAIN_ID}:' + address(value)


def block_evidence(block, receipts, observed_at):
    """Reject partial/mixed-block receipts. Preserve evidence for supported detectors.

    Compact projection is NOT an archive of all logs or full transaction calldata.
    """
    number, block_hash = quantity(block['number']), hash32(block['hash'])
    parent = hash32(block['parentHash'])
    txs = [hash32(h) for h in block['transactions']]
    if len(set(txs)) != len(txs) or not isinstance(receipts, list) or len(receipts) != len(txs):
        raise ValueError('incomplete receipt coverage')
    timestamp = quantity(block['timestamp'])
    if timestamp > observed_at.timestamp():
        raise ValueError('future chain timestamp')
    seen, logs_seen, observations = set(), set(), []
    for receipt in receipts:
        tx = hash32(receipt['transactionHash'])
        if tx not in txs or tx in seen or hash32(receipt['blockHash']) != block_hash or quantity(receipt['blockNumber']) != number:
            raise ValueError('mixed or duplicate receipt')
        seen.add(tx)
        status = quantity(receipt['status'])
        if status not in {0, 1} or not isinstance(receipt['logs'], list):
            raise ValueError('invalid receipt status/logs')
        if not status:
            if receipt['logs'] or receipt.get('contractAddress'):
                raise ValueError('failed transaction contains successful effects')
            continue
        created = receipt.get('contractAddress')
        if created:
            observations.append({'address': address(created), 'kind': 'DIRECT_DEPLOYMENT', 'tx_hash': tx, 'log_index': None})
        for log in receipt['logs']:
            if hash32(log['blockHash']) != block_hash or hash32(log['transactionHash']) != tx or quantity(log['blockNumber']) != number or log.get('removed') is not False:
                raise ValueError('unconfirmed or mixed-block log')
            index = quantity(log['logIndex'])
            if index in logs_seen:
                raise ValueError('duplicate log index')
            logs_seen.add(index)
            topics = log.get('topics')
            if not isinstance(topics, list):
                raise ValueError('invalid log topics')
            if len(topics) >= 3 and hash32(topics[0]) == TRANSFER and hash32(topics[1]) == ZERO:
                observations.append({'address': address(log['address']), 'kind': 'MINT_TRANSFER',
                    'tx_hash': tx, 'log_index': index, 'recipient': address('0x'+hash32(topics[2])[-40:])})
    observations.sort(key=lambda o: (o['address'], o['tx_hash'], -1 if o['log_index'] is None else o['log_index']))
    return {'chain_id': CHAIN_ID, 'number': number, 'hash': block_hash, 'parent_hash': parent,
            'source_time': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            'availability_time': observed_at.isoformat(), 'transaction_hashes': txs,
            'receipt_count': len(receipts), 'observations': observations,
            'coverage': 'DIRECT_DEPLOYMENT_AND_MINT_TRANSFER_ONLY', 'data_version': 'bsc-evidence-1'}
