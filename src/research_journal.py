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
from uuid import uuid4
from datetime import timedelta
try:
    from .retry_policy import utc, retry_at
except ImportError:
    from retry_policy import utc, retry_at


class RevisionConflict(RuntimeError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ResearchJournal:
    def __init__(self, path=None, connection=None, initialize=True, expected_environment=None):
        self.pg = connection is not None
        self.expected_environment=expected_environment or (None if self.pg else "local")
        if self.pg:
            self.db = connection
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path), timeout=10)
            self.db.execute("PRAGMA journal_mode=WAL")
        if initialize:
            self._init()
        if self.expected_environment:
            try:self.load()
            except Exception:
                self.db.close()
                raise

    def sql(self, query, args=()):
        if self.pg:
            # Transaction poolers reject timeout GUCs in the startup packet.
            # SET LOCAL belongs to this transaction and cannot leak to another client.
            from psycopg.pq import TransactionStatus
            if self.db.info.transaction_status == TransactionStatus.IDLE:
                with self.transaction():
                    return self.db.execute(query.replace("?", "%s"), args)
            return self.db.execute(query.replace("?", "%s"), args)
        return self.db.execute(query, args)

    @contextmanager
    def transaction(self):
        if self.pg:
            with self.db.transaction():
                self.db.execute("SET LOCAL statement_timeout=5000")
                self.db.execute("SET LOCAL lock_timeout=2000")
                yield
            return
        try:
            self.sql("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _init(self):
        if self.pg:
            raise RuntimeError("PostgreSQL requires the explicit runtime_ledger.sql migration")
        with self.transaction():
            self.sql("CREATE TABLE IF NOT EXISTS csr_runtime (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)")
            self.sql("INSERT INTO csr_runtime(id,revision,payload) VALUES (1,0,'{}') ON CONFLICT (id) DO NOTHING")
            self.sql("CREATE TABLE IF NOT EXISTS csr_records (kind TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,record_key))")
            self.sql("CREATE TABLE IF NOT EXISTS csr_validation_jobs (signal_id TEXT NOT NULL, horizon TEXT NOT NULL, due_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', last_error TEXT, PRIMARY KEY(signal_id,horizon))")
            self.sql("CREATE INDEX IF NOT EXISTS csr_validation_due ON csr_validation_jobs(status,due_at)")
            self.sql("CREATE TABLE IF NOT EXISTS csr_scheduler_wakes (operation TEXT PRIMARY KEY, token TEXT NOT NULL, due_at TEXT NOT NULL, status TEXT NOT NULL, lease_until TEXT)")
            if not self.pg:
                columns = {r[1] for r in self.sql("PRAGMA table_info(csr_validation_jobs)")}
                for name in ("next_attempt_at","lease_until","lease_token"):
                    if name not in columns:
                        self.sql(f"ALTER TABLE csr_validation_jobs ADD COLUMN {name} TEXT")
                runtime_columns = {r[1] for r in self.sql("PRAGMA table_info(csr_runtime)")}
                if "ledger_id" not in runtime_columns:
                    self.sql("ALTER TABLE csr_runtime ADD COLUMN ledger_id TEXT")
                if "environment" not in runtime_columns:
                    self.sql("ALTER TABLE csr_runtime ADD COLUMN environment TEXT")
                self.sql("UPDATE csr_runtime SET ledger_id=? WHERE ledger_id IS NULL",(str(uuid4()),))
                self.sql("UPDATE csr_runtime SET environment='local' WHERE environment IS NULL")
                self.sql("CREATE INDEX IF NOT EXISTS csr_validation_retry ON csr_validation_jobs(status,next_attempt_at,due_at)")
                self.sql("CREATE TRIGGER IF NOT EXISTS csr_records_no_update BEFORE UPDATE ON csr_records BEGIN SELECT RAISE(ABORT,'research records are immutable'); END")
                self.sql("CREATE TRIGGER IF NOT EXISTS csr_records_no_delete BEFORE DELETE ON csr_records BEGIN SELECT RAISE(ABORT,'research records are immutable'); END")

    def load(self):
        row = self.sql("SELECT revision,payload,ledger_id,environment FROM csr_runtime WHERE id=1").fetchone()
        if self.expected_environment and row[3]!=self.expected_environment:
            raise RuntimeError("research ledger environment mismatch")
        return row[0], dict(json.loads(row[1]),ledger_id=row[2],environment=row[3])

    def _insert(self, kind, key, row):
        payload = encoded(row)
        old = self.sql("SELECT payload FROM csr_records WHERE kind=? AND record_key=?", (kind,key)).fetchone()
        if old and old[0] != payload:
            raise ValueError(f"immutable record conflict: {kind}")
        self.sql("INSERT INTO csr_records VALUES (?,?,?) ON CONFLICT (kind,record_key) DO NOTHING", (kind,key,payload))

    def commit_cycle(self, expected_revision, payload, records):
        with self.transaction():
            suffix = " FOR UPDATE" if self.pg else ""
            revision,ledger_id,environment = self.sql("SELECT revision,ledger_id,environment FROM csr_runtime WHERE id=1"+suffix).fetchone()
            if self.expected_environment and environment!=self.expected_environment:
                raise RuntimeError("research ledger environment mismatch")
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
                    start = utc(row["observed_at"])
                    for horizon, hours in HORIZONS.items():
                        due = (start+timedelta(hours=hours,minutes=10)).isoformat()
                        self.sql("INSERT INTO csr_validation_jobs(signal_id,horizon,due_at) VALUES(?,?,?) ON CONFLICT(signal_id,horizon) DO NOTHING",(row["signal_id"],horizon,due))
            payload = dict(payload, revision=revision+1,ledger_id=ledger_id,environment=environment)
            self.sql("UPDATE csr_runtime SET revision=?,payload=? WHERE id=1",(revision+1,encoded(payload)))
        return payload

    def read(self, kind):
        return [json.loads(r[0]) for r in self.sql("SELECT payload FROM csr_records WHERE kind=? ORDER BY record_key",(kind,)).fetchall()]

    def record(self, kind, key):
        row = self.sql("SELECT payload FROM csr_records WHERE kind=? AND record_key=?",(kind,key)).fetchone()
        return json.loads(row[0]) if row else None

    def append_scheduler_receipt(self, row):
        # Operational evidence is append-only and does not advance model state.
        with self.transaction():
            self._insert("scheduler_receipts",str(uuid4()),row)

    def due_jobs(self, now, limit=20):
        stamp=utc(now).isoformat()
        return self.sql("SELECT signal_id,horizon FROM csr_validation_jobs WHERE status='pending' AND due_at<=? AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY COALESCE(next_attempt_at,due_at),signal_id,horizon LIMIT ?",(stamp,stamp,limit)).fetchall()

    def claim_due_jobs(self, now, limit=20, lease_now=None):
        from datetime import datetime,timezone
        stamp=utc(now).isoformat()
        lease_now=utc(lease_now or datetime.now(timezone.utc))
        lease_stamp=lease_now.isoformat()
        with self.transaction():
            lock=" FOR UPDATE SKIP LOCKED" if self.pg else ""
            rows=self.sql("SELECT signal_id,horizon FROM csr_validation_jobs WHERE due_at<=? AND ((status='pending' AND (next_attempt_at IS NULL OR next_attempt_at<=?)) OR (status='processing' AND lease_until<=?)) ORDER BY COALESCE(next_attempt_at,due_at),signal_id,horizon LIMIT ?"+lock,(stamp,stamp,lease_stamp,limit)).fetchall()
            claims=[]
            for sid,horizon in rows:
                token=str(uuid4())
                lease=(lease_now+timedelta(seconds=120)).isoformat()
                self.sql("UPDATE csr_validation_jobs SET status='processing',attempts=attempts+1,lease_token=?,lease_until=? WHERE signal_id=? AND horizon=?",(token,lease,sid,horizon))
                claims.append((sid,horizon,token))
        return claims

    def signal(self, key):
        row = self.sql("SELECT payload FROM csr_records WHERE kind='signals' AND record_key=?",(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def finish_validation(self, sid, horizon, row=None, error=None, lease_token=None, now=None):
        from datetime import datetime,timezone
        now=utc(now or datetime.now(timezone.utc))
        with self.transaction():
            lock=" FOR UPDATE" if self.pg else ""
            job=self.sql("SELECT status,attempts,lease_token,lease_until FROM csr_validation_jobs WHERE signal_id=? AND horizon=?"+lock,(sid,horizon)).fetchone()
            if not job or job[0]=='complete':
                return False
            if lease_token is not None:
                if job[0]!='processing' or job[2]!=lease_token or not job[3] or utc(job[3])<=now:
                    return False
            elif job[0]=='processing':
                return False
            if row is not None:
                # First successful outcome wins; a concurrent retry is a no-op.
                self.sql("INSERT INTO csr_records VALUES ('validations',?,?) ON CONFLICT(kind,record_key) DO NOTHING",(sid+':'+horizon,encoded(row)))
            attempts=job[1] if lease_token else job[1]+1
            next_at=None if row is not None else retry_at(now,attempts,sid+':'+horizon)
            self.sql("UPDATE csr_validation_jobs SET attempts=?,status=?,last_error=?,next_attempt_at=?,lease_token=NULL,lease_until=NULL WHERE signal_id=? AND horizon=?",(attempts,'complete' if row is not None else 'pending',error,next_at,sid,horizon))
            return True

    def close(self):
        self.db.close()


def open_journal(root="data/journal"):
    url = os.getenv("CSR_RESEARCH_DATABASE_URL")
    if url:
        import psycopg
        environment = os.getenv("VERCEL_ENV")
        if environment and os.getenv("CSR_RESEARCH_ENVIRONMENT") != environment:
            raise RuntimeError("research database environment must match VERCEL_ENV")
        label=os.getenv("CSR_RESEARCH_ENVIRONMENT")
        if label not in {"preview","production","test"}:
            raise RuntimeError("research database environment label is required")
        return ResearchJournal(connection=psycopg.connect(url, autocommit=True, connect_timeout=8),
            initialize=False,expected_environment=label)
    if os.getenv("VERCEL"):
        raise RuntimeError("durable research database required; configure CSR_RESEARCH_DATABASE_URL")
    return ResearchJournal(Path(root)/"research.sqlite3")
