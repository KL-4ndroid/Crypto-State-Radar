"""Evidence classification only; a bytecode reference is not exclusivity or safety."""
from pathlib import Path
import hashlib
from .contracts import AlgorithmIdentity, AlgorithmOutput, bind, restore
from ..bsc.contracts import entity
from ..v33.contracts import digest


class BSCDiscovery:
    def __init__(self):
        code = Path(__file__).read_text(encoding='utf-8') + Path(__file__).with_name('contracts.py').read_text(encoding='utf-8')
        self.identity = AlgorithmIdentity('bsc-discovery', 'BSC-READONLY-1', 'bsc-evidence-1',
            'bsc-project-1', hashlib.sha256(code.encode()).hexdigest(),
            digest({'execution_enabled':False}), 'OBSERVATION_ONLY')

    def evaluate(self, request):
        when, old = restore(self.identity, request)
        if not request.symbol.startswith('eip155:56:') or entity(request.symbol.split(':',2)[2]) != request.symbol:
            raise ValueError('BSC entity id required')
        values = request.values
        checked, reference = values.get('code_checked'), values.get('agent_id_reference')
        if checked is not True or not request.source_integrity:
            state, reasons = 'DATA_PENDING', ('CODE_OR_SOURCE_UNCONFIRMED',)
        elif reference is True:
            state, reasons = 'REVIEW_REQUIRED', ('REGISTRY_BYTECODE_REFERENCE', 'EXCLUSIVITY_NOT_PROVEN')
        else:
            state, reasons = 'OBSERVED', ('NO_DIRECT_REGISTRY_REFERENCE', 'PROXY_AND_BACKEND_NOT_RESOLVED')
        payload = {'state':state, 'execution_enabled':False}
        events = () if old and old['state'] == state else ({'type':'PROJECT_STATE', 'from':old['state'] if old else None, 'to':state},)
        return AlgorithmOutput(self.identity, request.symbol, when, {}, state, reasons,
            {'source_integrity':request.source_integrity, 'execution_enabled':False},
            bind(self.identity, request.symbol, when, payload), events, payload)
