"""Explicit built-in allowlist. No dynamic imports or remotely supplied code."""
from .v2 import V2Algorithm
from .v33 import V33Algorithm
from .bsc import BSCDiscovery
from ..v33.registry import defaults


def resolve(algorithm_id='radar-v2', *, rule=None, target='research'):
    if target not in {'research', 'live'}:
        raise ValueError('unknown algorithm target')
    if algorithm_id == 'radar-v2':
        if rule is not None:
            raise ValueError('V2 has no external rule file; update its versioned implementation')
        algorithm = V2Algorithm()
    elif algorithm_id == 'bsc-discovery':
        if rule is not None:
            raise ValueError('discovery policy has no trading rules')
        algorithm = BSCDiscovery()
    elif algorithm_id == 'distribution-v33':
        algorithm = V33Algorithm(rule if rule is not None else defaults())
    else:
        raise ValueError('unknown algorithm id')
    if target == 'live' and algorithm_id != 'radar-v2':
        # CHAMPION metadata alone is not evidence. Enable a reviewed adapter only
        # after producers, continuous shadow and execution validation exist.
        raise ValueError('V3.3 live adapter is not validated; use research/shadow evaluation')
    return algorithm
