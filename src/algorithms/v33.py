"""V3.3 pure adapter. Causal source verification remains the feature store's job."""
from copy import deepcopy
from pathlib import Path
import hashlib

from .contracts import AlgorithmIdentity, AlgorithmOutput, bind, restore
from ..v33.contracts import CONTRACT_VERSION, REFERENCE_FEATURES, digest, evaluation_key, snapshot
from ..v33.fsm import advance, STATE_VERSION
from ..v33.registry import validate_rule


class V33Algorithm:
    def __init__(self, rule):
        self._rule = deepcopy(validate_rule(deepcopy(rule)))
        root = Path(__file__).resolve().parents[1]
        # Bootstrap-only file reads; evaluate() is deterministic and performs no I/O.
        paths = ['algorithms/contracts.py', 'algorithms/v33.py', 'v33/contracts.py',
                 'v33/families.py', 'v33/fsm.py', 'v33/registry.py']
        source = b''.join(name.encode() + b'\0' + (root / name).read_text(encoding='utf-8').encode() for name in paths)
        self.identity = AlgorithmIdentity('distribution-v33', self._rule['rule_version'],
            CONTRACT_VERSION, STATE_VERSION, hashlib.sha256(source).hexdigest(),
            digest(self._rule), self._rule['status'])

    def evaluate(self, request):
        when, old = restore(self.identity, request)
        rows = deepcopy(list(request.feature_rows))
        snapshot_hash, values = snapshot(rows, request.symbol, when)
        references = REFERENCE_FEATURES | set(self._rule['parameters']['gate'].get('additional_past_only_features', []))
        if any(r['value'] is not None and r['feature_name'] in references and not r.get('reference_source_ids') for r in rows):
            raise ValueError('normalization feature requires past-only reference lineage')
        values['source_integrity'] = request.source_integrity is True
        key = evaluation_key(request.symbol, when, self.identity.model_version, snapshot_hash)
        state, result, events = advance(old, values, rows, when, key, self._rule['parameters'])
        result.update(evaluation_key=key, feature_snapshot_hash=snapshot_hash,
                      algorithm=self.identity.row())
        return AlgorithmOutput(self.identity, request.symbol, when,
            {'distribution': result['distribution_score'], 'squeeze_risk': result['squeeze_risk']},
            result['trade_state'], tuple(result['active_blocks']),
            {key: result[key] for key in ('score_breadth', 'dynamic_quality', 'execution_quality',
                                         'active_blocks', 'model_status', 'executable', 'signal_eligible')},
            bind(self.identity, request.symbol, when, state), tuple(events), result)
