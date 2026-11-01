"""Replay saved compact evidence, sharing the collector's pure projections."""
from .scanner import project_block,project_code
from .contracts import hash32
from ..algorithms.bsc import BSCDiscovery
from ..v33.contracts import stamp,digest


def replay(rows):
    algorithm=BSCDiscovery()
    rows=sorted(rows,key=lambda r:(stamp(r.get('availability_time') or r['observed_at']),r.get('kind','BLOCK'),digest(r)))
    projects,blocks,last={}, {}, None
    for row in rows:
        if row.get('kind')=='CODE':
            if blocks.get(row['block_number'])!=row['block_hash'] or row['entity_id'] not in projects:
                raise ValueError('code evidence lacks a prior matching block/entity')
            projects=project_code(projects,row,algorithm)
        else:
            if row.get('data_version')!='bsc-evidence-1' or row.get('chain_id')!=56 or stamp(row['source_time'])>stamp(row['availability_time']):
                raise ValueError('invalid block evidence contract')
            if last and (row.get('range_start',row['number'])!=last['number']+1 or row['parent_hash']!=last['hash']):
                raise ValueError('non-contiguous replay requires separate recovery stream')
            hash32(row['hash']);hash32(row['parent_hash'])
            blocks[row['number']]=row['hash']
            projects=project_block(projects,row,algorithm)
            last=row
    return {'algorithm':algorithm.identity.row(),'projects':projects,'blocks':len(blocks),
            'input_sha256':digest(rows),'execution_enabled':False,'scope':'saved compact evidence, not full-market coverage'}
