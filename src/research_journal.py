"""Transactional research ledger plus a revisioned snapshot publication outbox.

SQLite is for persistent local workers. PostgreSQL uses the same contract in
serverless environments. A scan does network work outside the DB transaction;
commit checks the revision so concurrent stale computations cannot overwrite it.
"""
from __future__ import annotations
import json
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager


class RevisionConflict(RuntimeError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ResearchJournal:
    def __init__(self, path=None, connection=None, initialize=True):
        self.pg = connection is not None
        if self.pg:
            self.db = connection
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path), timeout=10)
            self.db.execute("PRAGMA journal_mode=WAL")
        if initialize:
            self._init()

    def sql(self, query, args=()):
        return self.db.execute(query.replace("?", "%s") if self.pg else query, args)

    @contextmanager
    def transaction(self):
        try:
            self.sql("BEGIN" if self.pg else "BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _init(self):
        with self.transaction():
            self.sql("CREATE TABLE IF NOT EXISTS csr_runtime (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)")
            self.sql("INSERT INTO csr_runtime VALUES (1,0,'{}') ON CONFLICT (id) DO NOTHING")
            self.sql("CREATE TABLE IF NOT EXISTS csr_records (kind TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,record_key))")
            self.sql("CREATE TABLE IF NOT EXISTS csr_validation_jobs (signal_id TEXT NOT NULL, horizon TEXT NOT NULL, due_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', last_error TEXT, PRIMARY KEY(signal_id,horizon))")
            self.sql("CREATE INDEX IF NOT EXISTS csr_validation_due ON csr_validation_jobs(status,due_at)")

    def load(self):
        row = self.sql("SELECT revision,payload FROM csr_runtime WHERE id=1").fetchone()
        return row[0], json.loads(row[1])

    def _insert(self, kind, key, row):
        payload = encoded(row)
        old = self.sql("SELECT payload FROM csr_records WHERE kind=? AND record_key=?", (kind,key)).fetchone()
        if old and old[0] != payload:
            raise ValueError(f"immutable record conflict: {kind}")
        self.sql("INSERT INTO csr_records VALUES (?,?,?) ON CONFLICT (kind,record_key) DO NOTHING", (kind,key,payload))

    def commit_cycle(self, expected_revision, payload, records):
        with self.transaction():
            suffix = " FOR UPDATE" if self.pg else ""
            revision = self.sql("SELECT revision FROM csr_runtime WHERE id=1"+suffix).fetchone()[0]
            if revision != expected_revision:
                raise RevisionConflict("scan computed from an outdated snapshot; retry from latest state")
            for kind, key, row in records:
                self._insert(kind,key,row)
                if kind == "signals":
                    from datetime import datetime, timedelta
                    try:
                        from .validate_signals import HORIZONS
                    except ImportError:
                        from validate_signals import HORIZONS
                    start = datetime.fromisoformat(row["observed_at"])
                    for horizon, hours in HORIZONS.items():
                        due = (start+timedelta(hours=hours,minutes=10)).isoformat()
                        self.sql("INSERT INTO csr_validation_jobs(signal_id,horizon,due_at) VALUES(?,?,?) ON CONFLICT(signal_id,horizon) DO NOTHING",(row["signal_id"],horizon,due))
            payload = dict(payload, revision=revision+1)
            self.sql("UPDATE csr_runtime SET revision=?,payload=? WHERE id=1",(revision+1,encoded(payload)))
        return payload

    def read(self, kind):
        return [json.loads(r[0]) for r in self.sql("SELECT payload FROM csr_records WHERE kind=? ORDER BY record_key",(kind,)).fetchall()]

    def due_jobs(self, now, limit=20):
        return self.sql("SELECT signal_id,horizon FROM csr_validation_jobs WHERE status<>'complete' AND due_at<=? ORDER BY due_at,signal_id,horizon LIMIT ?",(now.isoformat(),limit)).fetchall()

    def signal(self, key):
        row = self.sql("SELECT payload FROM csr_records WHERE kind='signals' AND record_key=?",(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def finish_validation(self, sid, horizon, row=None, error=None):
        with self.transaction():
            if row is not None:
                # First successful outcome wins; a concurrent retry is a no-op.
                self.sql("INSERT INTO csr_records VALUES ('validations',?,?) ON CONFLICT(kind,record_key) DO NOTHING",(sid+':'+horizon,encoded(row)))
            self.sql("UPDATE csr_validation_jobs SET attempts=attempts+1,status=?,last_error=? WHERE signal_id=? AND horizon=? AND status<>'complete'",('complete' if row is not None else 'pending', error,sid,horizon))

    def close(self):
        self.db.close()


def open_journal(root="data/journal"):
    url = os.getenv("CSR_RESEARCH_DATABASE_URL")
    if url:
        import psycopg
        environment = os.getenv("VERCEL_ENV")
        if environment and os.getenv("CSR_RESEARCH_ENVIRONMENT") != environment:
            raise RuntimeError("research database environment must match VERCEL_ENV")
        return ResearchJournal(connection=psycopg.connect(url, autocommit=True, connect_timeout=8,
            options="-c statement_timeout=5000 -c lock_timeout=2000"),initialize=False)
    if os.getenv("VERCEL"):
        raise RuntimeError("durable research database required; configure CSR_RESEARCH_DATABASE_URL")
    return ResearchJournal(Path(root)/"research.sqlite3")
