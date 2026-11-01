from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from src.v33.backtest import ExecutionPolicy, simulate_short, purged_split, summarize

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(minutes):
    return (BASE + timedelta(minutes=minutes)).isoformat()


def fixture():
    signal = dict(symbol='BTCUSDT', signal_id='one', evaluation_time=at(0),
                  atr=2, atr_available_at=at(0), notional=100, executable=True,
                  funding_covered_from=at(0), funding_covered_until=at(60))
    bars = [dict(symbol='BTCUSDT', open_time=at(i), close_time=at(i+5),
                 open=100, high=101, low=99, close=100, quote_volume=100000)
            for i in (5, 10)]
    policy = ExecutionPolicy(horizon_seconds=600, fee_bps=0, slippage_bps=0)
    return signal, bars, policy


def run(signal=None, bars=None, policy=None, funding=None, observed=20):
    s, b, p = fixture()
    return simulate_short(signal or s, bars if bars is not None else b,
                          funding or [], at(observed), policy or p)


def test_next_price_and_costs():
    result = run(policy=replace(fixture()[2], fee_bps=4, slippage_bps=5))
    assert result['entry_time'] == at(5).replace('+00:00', '.000000+00:00')
    assert result['exit_reason'] == 'TIMEOUT'
    assert result['net_return'] < -.0017
    assert result['net_return'] == pytest.approx(result['gross_return'] - result['fee_return'])


def test_stop_first_and_adverse_gap():
    _, bars, _ = fixture()
    bars[0].update(high=105, low=90)
    result = run(bars=bars)
    assert result['exit_reason'] == 'STOP' and result['ambiguity'] is True
    assert result['net_return'] == pytest.approx(-.03)
    bars[0].update(high=101, low=99)
    bars[1].update(open=110, high=111, low=109, close=110)
    assert run(bars=bars)['net_return'] == pytest.approx(-.1)


def test_target_and_funding_sign():
    _, bars, _ = fixture()
    bars[1].update(low=95)
    result = run(bars=bars, funding=[dict(symbol='BTCUSDT', time=at(10), rate=.01, mark_price=100)])
    assert result['exit_reason'] == 'TARGET'
    assert result['net_return'] == pytest.approx(.05)


@pytest.mark.parametrize('status,change', [
    ('BLOCKED', {'executable': False}),
    ('FUNDING_GAP', {'funding_covered_until': at(10)}),
    ('FUNDING_GAP', {'funding_covered_from': at(10)}),
    ('CAPACITY_BLOCKED', {'notional': 100000}),
])
def test_exclusions_are_not_losses(status, change):
    signal, _, _ = fixture()
    signal.update(change)
    result = run(signal=signal)
    assert result['status'] == status and result['net_return'] is None


def test_unmatured_missing_and_future_paths():
    assert run(observed=7)['status'] == 'PENDING'
    assert run(bars=[])['status'] == 'NO_ENTRY'
    assert run(bars=[], observed=2)['status'] == 'PENDING'
    assert run(bars=fixture()[1][:1])['status'] == 'DATA_GAP'
    signal, bars, _ = fixture()
    bars[1]['open_time'], bars[1]['close_time'] = at(15), at(20)
    assert run(bars=bars)['status'] == 'DATA_GAP'
    signal['atr_available_at'] = at(1)
    with pytest.raises(ValueError, match='causal'):
        run(signal=signal)


def test_intrabar_funding_requires_finer_path():
    _, bars, _ = fixture()
    bars[0]['low'] = 95
    assert run(bars=bars, funding=[dict(symbol='BTCUSDT', time=at(7), rate=.01, mark_price=100)])['status'] == 'FUNDING_AMBIGUOUS'


def test_bad_input_and_determinism():
    assert run() == run()
    _, bars, _ = fixture()
    bars[0]['low'] = float('nan')
    with pytest.raises(ValueError):
        run(bars=bars)
    with pytest.raises(ValueError):
        ExecutionPolicy(horizon_seconds=301)
    with pytest.raises(ValueError):
        ExecutionPolicy(fee_bps=True)


def test_purge_labels_and_embargo():
    rows = [dict(sample_id=str(i), evaluation_time=at(i), label_end=at(i+5), mature=True)
            for i in (0, 5, 10, 15, 20, 25)]
    split = purged_split(rows, at(0), at(10), at(20), at(30))
    assert [r['sample_id'] for r in split['train']] == ['0']
    assert [r['sample_id'] for r in split['validation']] == ['10']
    assert [r['sample_id'] for r in split['test']] == ['20']
    assert len(split['excluded']) == 3
    assert not purged_split(rows, at(0), at(10), at(20), at(30), 300)['train']
    rows[0]['mature'] = False
    assert not purged_split(rows, at(0), at(10), at(20), at(30))['train']


def test_cluster_uncertainty_and_pending_denominator():
    results = [dict(signal_id=str(i), status='CLOSED', net_return=v)
               for i, v in enumerate((.1, .1, -.1))]
    results.append(dict(signal_id='pending', status='PENDING', net_return=None))
    clusters = {'0': 'day1/down', '1': 'day1/down', '2': 'day2/up'}
    report = summarize(results, clusters)
    assert report == summarize(results, clusters)
    assert report['raw_n'] == 3 and report['effective_n'] == 2
    assert report['executable_precision'] == 2/3
    assert report['unresolved_n'] == 1
    assert report['ci95'][0] <= report['estimate'] <= report['ci95'][1]
    assert summarize(results[:1], clusters)['ci95'] is None
    assert summarize([], {})['estimate'] is None
