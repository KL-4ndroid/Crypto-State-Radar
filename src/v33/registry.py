"""Immutable hypothesis versions and append-only lifecycle decisions."""
from pathlib import Path
import json
from .contracts import digest, stamp
from .families import numeric

RULE_FIELDS = {'rule_id','rule_version','status','hypothesis','feature_dependencies','parameters',
               'training_period','validation_period','test_period','metrics','promotion_decision'}
STATUSES = {'EXPERIMENTAL','CHALLENGER','CHAMPION','RETIRED'}


def validate_rule(rule):
    if RULE_FIELDS - rule.keys() or rule['status'] not in STATUSES:
        raise ValueError('incomplete rule registry entry')
    if not rule['rule_id'] or not rule['rule_version'] or not isinstance(rule['parameters'], dict):
        raise ValueError('invalid rule identity or parameters')
    p=rule['parameters']
    if not 0<p['family_valid_coverage']<=1 or not 0<=p['strong_family_score']<=100:
        raise ValueError('invalid family policy')
    if set(p['family_weights'])!={'F1','F2','F3','F4','F5'} or set(p['component_weights'])!=set(p['family_weights']):
        raise ValueError('five families are required by this contract version')
    for weights in [p['family_weights'],*p['component_weights'].values()]:
        if not weights or any(not numeric(w) or w<=0 for w in weights.values()):raise ValueError('invalid weights')
    for field in ('pump_confirm','pump_reset','leverage_confirm','saturation_confirm','recovery_confirm'):
        if type(p['phase'][field]) is not int or p['phase'][field]<1:raise ValueError('invalid confirmation count')
    for observation in p['phase']['confirmation_observations'].values():
        if not observation['feature'] or type(observation['seconds']) is not int or observation['seconds']<=0:
            raise ValueError('invalid confirmation observation policy')
    if type(p['trade']['ttl_seconds']) is not int or p['trade']['ttl_seconds']<=0:raise ValueError('invalid experimental TTL')
    if p['gate']['squeeze_on']<=p['gate']['squeeze_off'] or p['gate']['squeeze_clear_observations']<1:
        raise ValueError('invalid hysteresis')
    periods = []
    for name in ('training_period','validation_period','test_period'):
        period = rule[name]
        if period is not None:
            start,end = map(stamp, period)
            if start >= end:
                raise ValueError('invalid research period')
            periods.append((start,end))
    if any(a[1] > b[0] for a,b in zip(periods,periods[1:])):
        raise ValueError('research periods overlap or are out of order')
    if rule['status'] == 'CHAMPION':
        # A registry entry must point to evidence; never promote defaults implicitly.
        decision = rule['promotion_decision'] or {}
        if len(periods) != 3 or not rule['metrics'] or not decision.get('approved') or not decision.get('evidence_id'):
            raise ValueError('champion requires explicit research evidence and approval')
    digest(rule)  # Reject NaN and unserializable parameters.
    return rule


def defaults():
    return validate_rule(json.loads(Path(__file__).with_name('experimental.json').read_text(encoding='utf-8')))
