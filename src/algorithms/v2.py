"""Compatibility adapter: delegates existing V2 score AND lifecycle unchanged."""
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import hashlib

from .contracts import AlgorithmIdentity, AlgorithmOutput, StateMismatch, bind, restore
from .. import radar_engine
from ..model_contract import CONTRACT_VERSION, MODEL_VERSION, model_manifest
from ..state_machine import SymbolState, STATE_SCHEMA_VERSION, state_from_dict, transition
from ..v33.contracts import digest


class V2Algorithm:
    def __init__(self):
        self.manifest = model_manifest()
        code = self.manifest['source_sha256'].encode() + Path(__file__).read_text(encoding='utf-8').encode() + Path(__file__).with_name('contracts.py').read_text(encoding='utf-8').encode()
        self.identity = AlgorithmIdentity('radar-v2', MODEL_VERSION, str(CONTRACT_VERSION),
            str(STATE_SCHEMA_VERSION), hashlib.sha256(code).hexdigest(), digest(self.manifest), 'LEGACY_LIVE')

    def adopt_legacy(self, state, when):
        """Explicit one-time adoption of existing V2 snapshots, preserving anchors."""
        if state.model_version not in {None, '', self.identity.model_version}:
            raise StateMismatch('legacy state belongs to another model version')
        return bind(self.identity, state.symbol, when, asdict(state))

    def evaluate(self, request):
        when, previous = restore(self.identity, request)
        now = datetime.fromisoformat(when)
        old = state_from_dict(previous) if previous is not None else SymbolState(request.symbol)
        if old.symbol != request.symbol or old.model_version not in {None, '', self.identity.model_version}:
            raise StateMismatch('V2 payload belongs to another symbol or model')
        # All input containers are copied by the caller-independent request boundary.
        from copy import deepcopy
        values = deepcopy(request.values)
        decision = radar_engine.classify(request.symbol, values, now=now).to_dict()
        if decision['model_version'] != self.identity.model_version:
            raise StateMismatch('classifier returned a different model version')
        state, event = transition(old, decision, now)
        missing = dict(values.get('missing_reasons', {}))
        return AlgorithmOutput(self.identity, request.symbol, when,
            {'pump': decision['pump_score'], 'dump': decision['dump_score']}, state.state,
            tuple(decision['reasons']), {'missing_reasons': missing,
                'evaluation_status': state.evaluation_status, 'pending_reason': state.pending_reason},
            bind(self.identity, request.symbol, when, asdict(state)),
            (event,) if event else (), decision)
