"""Offline execution research. Prices are quote units; returns are fractions.

Bars describe [open_time, close_time]. Funding rate > 0 pays shorts.
No runtime imports, network requests, promotion or trading side effects.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import random
import statistics

from .contracts import stamp, digest

VERSION = 'v33-execution-1'


def time(value):
    return datetime.fromisoformat(stamp(value))


def number(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('invalid ' + name)
    if minimum is not None and value < minimum:
        raise ValueError('invalid ' + name)
    return float(value)


@dataclass(frozen=True)
class ExecutionPolicy:
    # Explicit bootstrap hypotheses, never calibrated with the test partition.
    stop_atr: float = 1.5
    target_atr: float = 2.0
    horizon_seconds: int = 43200
    bar_seconds: int = 300
    fee_bps: float = 4
    slippage_bps: float = 5
    max_participation: float = 0.01
    max_entry_delay_seconds: int = 300

    def __post_init__(self):
        for key in ('stop_atr', 'target_atr'):
            if number(getattr(self, key), key, 0) == 0:
                raise ValueError('positive barrier required')
        for key in ('horizon_seconds', 'bar_seconds', 'max_entry_delay_seconds'):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError('positive integer time policy required')
        for key in ('fee_bps', 'slippage_bps'):
            if number(getattr(self, key), key, 0) >= 10000:
                raise ValueError('cost must be below 100%')
        if not 0 < number(self.max_participation, 'participation') <= 1:
            raise ValueError('invalid participation')
        if self.horizon_seconds % self.bar_seconds:
            raise ValueError('horizon must align with bars')


def simulate_short(signal, bars, funding, observed_until, policy=None):
    """Next bar strictly after evaluation; adverse gaps fill at the worse open.

    signal needs symbol, signal_id, evaluation_time, atr, atr_available_at,
    notional, executable, funding_covered_from and funding_covered_until.
    Funding coverage includes intervals with no settlements; [] alone proves none.
    Bar quote_volume is an ex-post capacity check, never an entry predictor.
    """
    policy = policy or ExecutionPolicy()
    evaluation = time(signal['evaluation_time'])
    observed = time(observed_until)
    if observed < evaluation:
        raise ValueError('observation cutoff precedes signal')
    atr = number(signal['atr'], 'ATR', 0)
    notional = number(signal['notional'], 'notional', 0)
    if not atr or not notional or time(signal['atr_available_at']) > evaluation:
        raise ValueError('positive causal ATR and notional required')
    if type(signal['executable']) is not bool:
        raise ValueError('explicit executable flag required')
    result = dict(version=VERSION, signal_id=signal['signal_id'], symbol=signal['symbol'],
                  input_hash=digest(dict(signal=signal, bars=bars, funding=funding,
                                         observed_until=stamp(observed), policy=policy.__dict__)),
                  model_status='NOT_VALIDATED', status=None, net_return=None,
                  ambiguity=False, capacity_basis='EX_POST_BAR_VOLUME')

    def finish(status, **fields):
        row = dict(result, status=status, **fields)
        row['result_id'] = digest(dict(signal=signal, policy=policy.__dict__, result=row))
        return row

    if not signal['executable']:
        return finish('BLOCKED')
    rows = []
    previous = None
    for bar in bars:
        if bar['symbol'] != signal['symbol']:
            raise ValueError('wrong symbol bar')
        start, end = time(bar['open_time']), time(bar['close_time'])
        if end - start != timedelta(seconds=policy.bar_seconds) or (previous and start <= previous):
            raise ValueError('invalid bar order or duration')
        previous = start
        o, h, l, c = [number(bar[k], k, 0) for k in ('open', 'high', 'low', 'close')]
        if l <= 0 or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError('invalid OHLC')
        number(bar['quote_volume'], 'quote volume', 0)
        if start > evaluation:
            rows.append(bar)
    if not rows:
        return finish('PENDING' if observed < evaluation + timedelta(seconds=policy.max_entry_delay_seconds) else 'NO_ENTRY')
    entry_time = time(rows[0]['open_time'])
    if entry_time > observed:
        return finish('PENDING')
    if (entry_time - evaluation).total_seconds() > policy.max_entry_delay_seconds:
        return finish('NO_ENTRY')
    slip = policy.slippage_bps / 10000
    entry = rows[0]['open'] * (1 - slip)
    stop, target = entry + policy.stop_atr * atr, entry - policy.target_atr * atr
    if target <= 0:
        raise ValueError('nonpositive target')
    horizon = entry_time + timedelta(seconds=policy.horizon_seconds)
    exit_time = None
    exit_price = None
    exit_reason = None
    mae = mfe = 0.0
    expected = entry_time
    for bar in rows:
        start, end = time(bar['open_time']), time(bar['close_time'])
        if start >= horizon or end > observed:
            break
        if start != expected:
            return finish('DATA_GAP')
        expected = end
        # Capacity is checked on every potential entry/exit bar. No favourable
        # assumption about resting fills is made from candle volume alone.
        if notional > bar['quote_volume'] * policy.max_participation:
            return finish('CAPACITY_BLOCKED')
        hit_stop, hit_target = bar['high'] >= stop, bar['low'] <= target
        mae = max(mae, (bar['high'] - entry) / entry)
        mfe = max(mfe, (entry - bar['low']) / entry)
        if hit_stop:
            exit_price, exit_reason = max(stop, bar['open']), 'STOP'
            result['ambiguity'] = hit_target
        elif hit_target:
            # A favourable opening gap is not credited beyond the target.
            exit_price, exit_reason = target, 'TARGET'
        elif end == horizon:
            exit_price, exit_reason = bar['close'], 'TIMEOUT'
        if exit_reason:
            exit_time = end
            break
    if exit_reason is None:
        return finish('PENDING' if observed < horizon else 'DATA_GAP')
    if time(signal['funding_covered_from']) > entry_time or time(signal['funding_covered_until']) < exit_time:
        return finish('FUNDING_GAP')
    funding_return = 0.0
    seen = set()
    for settlement in funding:
        if settlement['symbol'] != signal['symbol']:
            raise ValueError('wrong funding symbol')
        at = time(settlement['time'])
        if at in seen:
            raise ValueError('duplicate funding settlement')
        seen.add(at)
        rate = number(settlement['rate'], 'funding rate')
        mark = number(settlement['mark_price'], 'funding mark', 0)
        if mark <= 0:
            raise ValueError('positive funding mark required')
        if entry_time < at <= exit_time:
            if exit_reason != 'TIMEOUT' and exit_time - timedelta(seconds=policy.bar_seconds) < at < exit_time:
                return finish('FUNDING_AMBIGUOUS')
            funding_return += rate * mark / entry
    exit_price *= 1 + slip
    gross = (entry - exit_price) / entry
    fee = policy.fee_bps / 10000 * (1 + exit_price / entry)
    return finish('CLOSED', entry_time=stamp(entry_time), exit_time=stamp(exit_time),
                  entry_price=entry, exit_price=exit_price, exit_reason=exit_reason,
                  gross_return=gross, fee_return=fee, funding_return=funding_return,
                  net_return=gross - fee + funding_return, mae=mae, mfe=mfe,
                  excursion_basis='FULL_EXIT_BAR_BOUND', intrabar_funding_order='EXIT_AT_BAR_END')


def purged_split(samples, train_start, validation_start, test_start, test_end, embargo_seconds=0):
    """Partition by signal time; purge labels overlapping the next partition.

    Labels must provide their full end time, including maximum holding horizon.
    Unmatured samples are excluded. Embargo is a gap before each later partition.
    """
    a, b, c, d = map(time, (train_start, validation_start, test_start, test_end))
    if not a < b < c < d or type(embargo_seconds) is not int or embargo_seconds < 0:
        raise ValueError('invalid chronological split')
    gap = timedelta(seconds=embargo_seconds)
    out = dict(train=[], validation=[], test=[], excluded=[])
    ids = set()
    for row in samples:
        if row['sample_id'] in ids:
            raise ValueError('duplicate sample')
        ids.add(row['sample_id'])
        start, end = time(row['evaluation_time']), time(row['label_end'])
        if end < start:
            raise ValueError('label precedes signal')
        bucket = 'excluded'
        if row.get('mature') is True:
            if a <= start < b and end < b - gap:
                bucket = 'train'
            elif b <= start < c and end < c - gap:
                bucket = 'validation'
            elif c <= start < d and end < d:
                bucket = 'test'
        out[bucket].append(row)
    return out


def summarize(results, cluster_by_signal, draws=1000, seed=0):
    """Day/regime cluster bootstrap, resampling whole clusters (equal rows).

    effective_n is the conservative number of independent supplied clusters,
    not a claim of statistically estimated sample independence.
    """
    if type(draws) is not int or draws < 100:
        raise ValueError('at least 100 bootstrap draws required')
    closed = [r for r in results if r['status'] == 'CLOSED']
    if len({r['signal_id'] for r in results}) != len(results):
        raise ValueError('duplicate results')
    groups = {}
    for row in closed:
        key = cluster_by_signal[row['signal_id']]
        if not isinstance(key, str) or not key:
            raise ValueError('explicit day/regime cluster required')
        groups.setdefault(key, []).append(number(row['net_return'], 'net return'))
    values = [v for group in groups.values() for v in group]
    estimates, precision = [], []
    rng = random.Random(seed)
    keys = sorted(groups)
    if len(keys) >= 2:
        for _ in range(draws):
            sample = [v for key in rng.choices(keys, k=len(keys)) for v in groups[key]]
            estimates.append(statistics.mean(sample))
            precision.append(sum(v > 0 for v in sample) / len(sample))

    def ci(items):
        if not items:
            return None
        items.sort()
        return [items[int((len(items) - 1) * p)] for p in (.025, .975)]

    return dict(version=VERSION, model_status='NOT_VALIDATED', raw_n=len(closed),
                effective_n=len(keys), effective_n_basis='SUPPLIED_CLUSTER_COUNT',
                estimate=statistics.mean(values) if values else None,
                ci95=ci(estimates), executable_precision=sum(v > 0 for v in values)/len(values) if values else None,
                precision_ci95=ci(precision), unresolved_n=sum(r['status'] not in {'CLOSED', 'BLOCKED'} for r in results),
                status_counts={s: sum(r['status'] == s for r in results) for s in sorted({r['status'] for r in results})},
                uncertainty='INSUFFICIENT_CLUSTERS' if len(keys) < 2 else 'CLUSTER_BOOTSTRAP')
