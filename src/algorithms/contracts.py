"""Application-independent evaluation and version-bound state contract."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Protocol

from ..v33.contracts import digest, stamp

API_VERSION = 'algorithm-api-1'


class StateMismatch(ValueError):
    """State requires an explicit migration or a separate model stream."""


@dataclass(frozen=True)
class AlgorithmIdentity:
    algorithm_id: str
    model_version: str
    feature_contract: str
    state_schema: str
    implementation_sha256: str
    policy_sha256: str
    status: str

    def row(self):
        return dict(asdict(self), api_version=API_VERSION)

    @property
    def artifact_id(self):
        return digest(self.row())


@dataclass(frozen=True)
class AlgorithmInput:
    symbol: str
    evaluation_time: datetime | str
    values: dict = field(default_factory=dict)
    feature_rows: tuple = ()
    previous_state: dict | None = None
    source_integrity: bool = False


@dataclass(frozen=True)
class AlgorithmOutput:
    identity: AlgorithmIdentity
    symbol: str
    evaluation_time: str
    scores: dict
    state: str
    reasons: tuple
    quality: dict
    next_state: dict
    events: tuple
    native: dict

    def row(self):
        row = asdict(self)
        row['identity'] = self.identity.row()
        return row


class Algorithm(Protocol):
    identity: AlgorithmIdentity

    def evaluate(self, request: AlgorithmInput) -> AlgorithmOutput: ...


def restore(identity, request):
    """Reject implicit cross-symbol/version/policy state adoption."""
    if not isinstance(request.symbol, str) or not request.symbol:
        raise ValueError('symbol required')
    when = stamp(request.evaluation_time)
    envelope = deepcopy(request.previous_state)
    if envelope is None:
        return when, None
    if envelope.get('identity') != identity.row() or envelope.get('symbol') != request.symbol:
        raise StateMismatch('algorithm state identity differs; use a separate stream or explicit migration')
    if stamp(envelope['evaluated_at']) >= when:
        raise StateMismatch('state must precede this evaluation; deduplicate in the journal')
    return when, envelope['payload']


def bind(identity, symbol, when, payload):
    return {'identity': identity.row(), 'symbol': symbol, 'evaluated_at': stamp(when),
            'payload': deepcopy(payload)}
