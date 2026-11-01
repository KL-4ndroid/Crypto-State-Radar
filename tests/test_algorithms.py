from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import subprocess
import sys

import pytest
from hypothesis import given, settings, strategies as st

from src.algorithms.contracts import AlgorithmInput, StateMismatch, bind
from src.algorithms.registry import resolve
from src.algorithms.v33 import V33Algorithm
from src.model_contract import MODEL_VERSION
from src.radar_engine import classify
from src.state_machine import SymbolState, transition
from src.v33.registry import defaults
from src.v33.fsm import initial, advance
from src.v33.contracts import stamp, snapshot, evaluation_key

BASE = datetime(2026, 11, 1, tzinfo=timezone.utc)


@settings(max_examples=80, deadline=None)
@given(st.lists(st.fixed_dictionaries({name: st.one_of(st.none(), st.floats(-10, 10, allow_nan=False))
    for name in ('oi_change_1h', 'oi_change_4h', 'oi_change_24h', 'price_change_1h',
                 'price_change_4h', 'funding_z', 'funding_acceleration', 'volume_z',
                 'volatility_compression')}), min_size=1, max_size=8))
def test_v2_adapter_preserves_decisions_events_and_lifecycle_across_replay(frames):
    algorithm = resolve('radar-v2')
    previous, envelope = SymbolState('A'), None
    for i, values in enumerate(frames):
        now = BASE + timedelta(minutes=5*i)
        values = dict(values, price=100+i, open_interest=10000)
        untouched = deepcopy(values)
        expected = classify('A', values, now).to_dict()
        previous, event = transition(previous, expected, now)
        actual = algorithm.evaluate(AlgorithmInput('A', now, values=values, previous_state=envelope))
        assert actual.native == expected
        assert actual.next_state['payload'] == asdict(previous)
        assert actual.events == ((event,) if event else ())
        assert values == untouched
        envelope = actual.next_state


def test_state_is_bound_to_symbol_version_code_policy_and_time():
    v2, v3 = resolve('radar-v2'), resolve('distribution-v33')
    state = v2.evaluate(AlgorithmInput('A', BASE)).next_state
    request = AlgorithmInput('A', BASE+timedelta(minutes=5), previous_state=state)
    with pytest.raises(StateMismatch):
        v3.evaluate(request)
    with pytest.raises(StateMismatch):
        v2.evaluate(replace(request, symbol='B'))
    with pytest.raises(StateMismatch):
        v2.evaluate(replace(request, evaluation_time=BASE))
    for field in ('model_version', 'implementation_sha256', 'policy_sha256', 'feature_contract', 'state_schema'):
        changed = deepcopy(state)
        changed['identity'][field] = 'different'
        with pytest.raises(StateMismatch):
            v2.evaluate(replace(request, previous_state=changed))


def test_legacy_adoption_preserves_ready_anchor_and_refuses_other_versions():
    algorithm = resolve('radar-v2')
    old = SymbolState('A', state='SHORT_READY', model_version=MODEL_VERSION,
        ready_price=100, ready_oi=10000, entered_at=BASE.isoformat(),
        expires_at=(BASE+timedelta(hours=4)).isoformat())
    envelope = algorithm.adopt_legacy(old, BASE)
    assert envelope['payload'] == asdict(old)
    with pytest.raises(StateMismatch):
        algorithm.adopt_legacy(replace(old, model_version='V3.3'), BASE)


def test_v33_adapter_matches_existing_fsm_without_mutation_or_io(monkeypatch):
    rule = defaults()
    algorithm = V33Algorithm(rule)
    before_rule = deepcopy(rule)
    rows = [dict(feature_name='close_5m', value=100, symbol='A', source_time=stamp(BASE),
                 asof_time=stamp(BASE), availability_time=stamp(BASE))]
    shash, values = snapshot(rows, 'A', BASE)
    values['source_integrity'] = True
    key = evaluation_key('A', BASE, rule['rule_version'], shash)
    expected_state, expected_result, expected_events = advance(None, values, rows, BASE, key, rule['parameters'])
    request = AlgorithmInput('A', BASE, feature_rows=tuple(rows), source_integrity=True)
    untouched = deepcopy(request)
    def forbidden(*a, **kw):
        raise AssertionError('pure evaluation attempted I/O')
    monkeypatch.setattr('builtins.open', forbidden)
    monkeypatch.setattr('pathlib.Path.open', forbidden)
    monkeypatch.setattr('socket.socket', forbidden)
    actual = algorithm.evaluate(request)
    assert {k: actual.native[k] for k in expected_result} == expected_result
    assert actual.next_state['payload'] == expected_state
    assert actual.events == tuple(expected_events)
    assert request == untouched and rule == before_rule
    assert algorithm.evaluate(request) == actual


def test_v33_rule_is_defensively_copied_and_future_features_rejected():
    rule = defaults()
    algorithm = V33Algorithm(rule)
    identity = algorithm.identity
    rule['parameters']['trade']['ready_score'] = 1
    assert algorithm.identity == identity
    changed = V33Algorithm(rule)
    assert changed.identity.policy_sha256 != identity.policy_sha256
    old = bind(identity, 'A', BASE, initial())
    with pytest.raises(StateMismatch):
        changed.evaluate(AlgorithmInput('A', BASE+timedelta(minutes=5), previous_state=old))
    future = dict(feature_name='close_5m', value=100, symbol='A', source_time=stamp(BASE),
                  asof_time=stamp(BASE), availability_time=stamp(BASE+timedelta(seconds=1)))
    with pytest.raises(ValueError, match='future'):
        algorithm.evaluate(AlgorithmInput('A', BASE, feature_rows=(future,)))


def test_registry_is_explicit_and_unvalidated_v33_cannot_be_selected_live():
    for name in ('unknown', 'src.radar_engine', '../model.py'):
        with pytest.raises(ValueError, match='unknown'):
            resolve(name)
    with pytest.raises(ValueError, match='not validated'):
        resolve('distribution-v33', target='live')
    assert resolve('radar-v2', target='live').identity.model_version == MODEL_VERSION


def test_invalid_live_selection_stops_before_storage_and_market(monkeypatch):
    from src import run_cycle
    monkeypatch.setenv('CSR_ALGORITHM_ID', 'distribution-v33')
    def forbidden(*a, **kw):
        pytest.fail('invalid model attempted storage or market access')
    monkeypatch.setattr(run_cycle, 'open_journal', forbidden)
    monkeypatch.setattr(run_cycle, 'collect_market', forbidden)
    with pytest.raises(ValueError, match='not validated'):
        run_cycle.run()


def test_saved_algorithm_binding_mismatch_stops_before_market_and_commit(monkeypatch, tmp_path):
    from src import run_cycle
    from src.research_journal import ResearchJournal
    monkeypatch.delenv('CSR_ALGORITHM_ID', raising=False)
    journal = ResearchJournal(tmp_path/'research.sqlite3')
    identity = resolve('radar-v2').identity.row()
    identity['policy_sha256'] = 'other-version'
    journal.commit_cycle(0, {'states': {}, 'algorithm': identity}, [])
    journal.close()
    def forbidden(*a, **kw):
        pytest.fail('mismatched model attempted market access')
    monkeypatch.setattr(run_cycle, 'collect_market', forbidden)
    with pytest.raises(StateMismatch):
        run_cycle.run(root=str(tmp_path))
    journal = ResearchJournal(tmp_path/'research.sqlite3')
    try:
        assert journal.load()[0] == 1
        assert journal.read('signals') == []
    finally:
        journal.close()


def test_inspection_cli_lists_models_and_rejects_premature_live_switch():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, 'tools/algorithms.py', 'list'], cwd=root,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert [r['algorithm_id'] for r in rows] == ['radar-v2', 'distribution-v33']
    assert [r['live_enabled'] for r in rows] == [True, False]
    blocked = subprocess.run([sys.executable, 'tools/algorithms.py', 'check', '--target', 'live'],
                             cwd=root, capture_output=True, text=True)
    assert blocked.returncode != 0 and 'not validated' in blocked.stderr
