"""Pure causal and identity contracts; no trading thresholds live here."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math

NAMESPACES = {'PRODUCTION', 'SHADOW', 'REPLAY', 'BACKTEST'}
CONTRACT_VERSION = 'v33-contract-1'
# Causal feature identities are contracts, not tunable numerical hypotheses.
REFERENCE_FEATURES = {'return_24h_p95','oi_1h_p75','oi_4h_p75','F4.funding_percentile'}


def stamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise ValueError('an aware timestamp is required')
    return dt.astimezone(timezone.utc).isoformat(timespec='microseconds')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def scope(namespace, run_id):
    if namespace not in NAMESPACES or not isinstance(run_id, str) or not run_id:
        raise ValueError('explicit namespace and run_id required')
    if namespace == 'PRODUCTION' and run_id != 'live':
        raise ValueError('production is reserved for the live stream')


@dataclass(frozen=True)
class RawObservation:
    source: str
    symbol: str
    raw_timestamp: str | int
    effective_time: str | None
    availability_time: str
    ingested_at: str
    source_revision: str
    payload: dict
    availability_basis: str = 'OBSERVED'
    source_integrity: bool = True
    data_version: str = 'v33-data-1'

    def row(self):
        row = asdict(self)
        if type(self.source_integrity) is not bool or isinstance(self.raw_timestamp,bool):
            raise ValueError('invalid source metadata types')
        for field in ('availability_time', 'ingested_at'):
            row[field] = stamp(row[field])
        if self.effective_time is not None:
            row['effective_time'] = stamp(self.effective_time)
            if row['effective_time'] > row['availability_time']:
                raise ValueError('data cannot be available before effective time')
        if row['availability_time'] > row['ingested_at']:
            raise ValueError('ingestion cannot precede availability')
        if self.availability_basis not in {'OBSERVED', 'ARCHIVE_PUBLISHED', 'ASSUMED'}:
            raise ValueError('unknown availability basis')
        if not all((self.source, self.symbol, self.source_revision, self.data_version)):
            raise ValueError('missing provenance')
        row['record_id'] = digest(row)
        return row


@dataclass(frozen=True)
class Feature:
    feature_name: str
    feature_version: str
    symbol: str
    asof_time: str
    source_time: str
    availability_time: str
    value: object
    source_ids: tuple[str, ...]
    missing_reason: str | None = None
    reference_source_ids: tuple[str, ...] = ()

    def row(self):
        row = asdict(self)
        row['source_ids'] = sorted(set(self.source_ids))
        row['reference_source_ids'] = sorted(set(self.reference_source_ids))
        if not set(row['reference_source_ids'])<=set(row['source_ids']):
            raise ValueError('reference lineage must be included in source lineage')
        for field in ('asof_time', 'source_time', 'availability_time'):
            row[field] = stamp(row[field])
        if row['source_time'] > row['asof_time'] or row['asof_time'] > row['availability_time']:
            raise ValueError('feature time order violated')
        if not self.feature_name or not self.feature_version or not self.symbol or not row['source_ids']:
            raise ValueError('feature lineage and version required')
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError('non-finite feature')
        if self.value is None and not self.missing_reason:
            raise ValueError('missing feature requires a reason')
        row['feature_id'] = digest(row)
        return row


def snapshot(features, symbol, evaluation_time):
    when = stamp(evaluation_time)
    rows = sorted(features, key=lambda row: row['feature_name'])
    if len({r['feature_name'] for r in rows}) != len(rows):
        raise ValueError('duplicate feature name in snapshot')
    for row in rows:
        if row['symbol'] != symbol or stamp(row['availability_time']) > when or stamp(row['asof_time']) > when or stamp(row['source_time'])>stamp(row['asof_time']) or stamp(row['asof_time'])>stamp(row['availability_time']):
            raise ValueError('future or wrong-symbol feature')
    return digest(rows), {r['feature_name']: r['value'] for r in rows}


def evaluation_key(symbol, when, rule_version, snapshot_hash):
    return digest([symbol, stamp(when), rule_version, snapshot_hash])
