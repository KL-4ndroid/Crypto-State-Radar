"""Bounded independent streams on the existing immutable journal; no new schema.

Only short commits share the existing DB row lock. No runtime revision, signal
jobs or Futures payload is changed. Caps measure JSON characters, not DB bills.
"""
import json
import re
from .research_journal import encoded, RevisionConflict
from .v33.contracts import digest


class StreamCapacity(OSError):
    pass


class RecordStream:
    MAX_BYTES = 16 * 1024 * 1024
    MAX_RECORDS = 10000

    def __init__(self, journal, name, max_bytes=MAX_BYTES, max_records=MAX_RECORDS):
        if not re.fullmatch(r'[a-z0-9:-]{1,100}', name):
            raise ValueError('invalid stream name')
        if not 1 <= max_bytes <= self.MAX_BYTES or not 1 <= max_records <= self.MAX_RECORDS:
            raise ValueError('stream capacity exceeds hard ceiling')
        self.journal, self.kind = journal, 'stream:' + name
        self.max_bytes, self.max_records = max_bytes, max_records
        row = journal.sql('SELECT ledger_id,environment FROM csr_runtime WHERE id=1').fetchone()
        self.ledger_id, self.environment = digest([row[0], name]), row[1]

    def stats(self):
        count, size = self.journal.sql('SELECT COUNT(*),COALESCE(SUM(LENGTH(payload)),0) FROM csr_records WHERE kind=?', (self.kind,)).fetchone()
        return {'records': count, 'logical_json_characters': size,
                'max_records': self.max_records, 'max_logical_characters': self.max_bytes}

    def load(self):
        row = self.journal.sql('SELECT payload FROM csr_records WHERE kind=? AND record_key LIKE ? ORDER BY record_key DESC LIMIT 1', (self.kind,'head:%')).fetchone()
        payload = json.loads(row[0]) if row else {}
        return payload.get('revision', 0), dict(payload, ledger_id=self.ledger_id, environment=self.environment)

    def read(self, key):
        return self.journal.record(self.kind, key)

    def commit(self, expected_revision, payload, records):
        if len(records) > 1000 or any(key.startswith('head:') for key, _ in records):
            raise ValueError('invalid stream batch')
        with self.journal.transaction():
            self.journal.sql('SELECT id FROM csr_runtime WHERE id=1' + (' FOR UPDATE' if self.journal.pg else '')).fetchone()
            revision, _ = self.load()
            if revision != expected_revision:
                raise RevisionConflict('stream revision changed')
            output = dict(payload, revision=revision+1, ledger_id=self.ledger_id, environment=self.environment)
            pending = dict(records)
            if len(pending) != len(records):
                raise ValueError('duplicate record key in stream batch')
            pending['head:' + str(revision+1).zfill(12)] = output
            new = []
            for key, row in pending.items():
                old = self.read(key)
                if old is not None and encoded(old) != encoded(row):
                    raise ValueError('immutable stream record conflict')
                if old is None:
                    new.append((key, row))
            stats = self.stats()
            size = sum(len(encoded(row)) for _, row in new)
            if stats['records']+len(new) > self.max_records or stats['logical_json_characters']+size > self.max_bytes:
                raise StreamCapacity('stream archive capacity exhausted')
            for key, row in new:
                self.journal._insert(self.kind, key, row)
        return output
