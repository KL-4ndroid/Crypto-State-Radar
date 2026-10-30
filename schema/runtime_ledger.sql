-- Isolated runtime ledger, additive to the legacy normalized research schema.
-- Apply explicitly using a migration role before enabling new requests.
CREATE TABLE IF NOT EXISTS csr_runtime (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
INSERT INTO csr_runtime(id,revision,payload) VALUES (1,0,'{}') ON CONFLICT (id) DO NOTHING;
CREATE TABLE IF NOT EXISTS csr_records (kind TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,record_key));
CREATE TABLE IF NOT EXISTS csr_validation_jobs (
 signal_id TEXT NOT NULL, horizon TEXT NOT NULL, due_at TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', last_error TEXT,
 PRIMARY KEY(signal_id,horizon)
);
CREATE INDEX IF NOT EXISTS csr_validation_due ON csr_validation_jobs(status,due_at);
-- Additive and rerunnable upgrade from checkpoint 4f50dd3.
ALTER TABLE csr_runtime ADD COLUMN IF NOT EXISTS ledger_id TEXT;
ALTER TABLE csr_runtime ADD COLUMN IF NOT EXISTS environment TEXT;
UPDATE csr_runtime SET ledger_id=md5(random()::text || clock_timestamp()::text) WHERE ledger_id IS NULL;
ALTER TABLE csr_validation_jobs ADD COLUMN IF NOT EXISTS next_attempt_at TEXT;
ALTER TABLE csr_validation_jobs ADD COLUMN IF NOT EXISTS lease_until TEXT;
ALTER TABLE csr_validation_jobs ADD COLUMN IF NOT EXISTS lease_token TEXT;
CREATE INDEX IF NOT EXISTS csr_validation_retry ON csr_validation_jobs(status,next_attempt_at,due_at);
CREATE OR REPLACE FUNCTION csr_reject_record_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'research records are immutable'; END;
$$;
DROP TRIGGER IF EXISTS csr_immutable_record ON csr_records;
CREATE TRIGGER csr_immutable_record BEFORE UPDATE OR DELETE ON csr_records
FOR EACH ROW EXECUTE FUNCTION csr_reject_record_mutation();
-- Runtime role: SELECT/INSERT on csr_records, SELECT and UPDATE(revision,payload) on csr_runtime,
-- SELECT/INSERT/UPDATE on csr_validation_jobs. No DDL or record UPDATE/DELETE.
