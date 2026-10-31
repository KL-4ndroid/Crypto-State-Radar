-- Additive; explicit migration only. Existing csr_records stores immutable raw
-- observations and registry entries under v33:<namespace>:<run_id>:<kind>.
CREATE TABLE IF NOT EXISTS csr_v33_features (
 namespace TEXT NOT NULL CHECK(namespace IN ('PRODUCTION','SHADOW','REPLAY','BACKTEST')),
 run_id TEXT NOT NULL, feature_id TEXT NOT NULL, feature_name TEXT NOT NULL,
 feature_version TEXT NOT NULL, symbol TEXT NOT NULL,
 asof_time TEXT NOT NULL, availability_time TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(namespace,run_id,feature_id)
);
CREATE INDEX IF NOT EXISTS csr_v33_feature_asof ON csr_v33_features(namespace,run_id,symbol,feature_name,availability_time,asof_time);
CREATE TABLE IF NOT EXISTS csr_v33_heads (
 namespace TEXT NOT NULL CHECK(namespace IN ('PRODUCTION','SHADOW','REPLAY','BACKTEST')),
 run_id TEXT NOT NULL, symbol TEXT NOT NULL, rule_version TEXT NOT NULL,
 revision INTEGER NOT NULL, last_time TEXT, payload TEXT NOT NULL,
 PRIMARY KEY(namespace,run_id,symbol,rule_version)
);
CREATE TABLE IF NOT EXISTS csr_v33_evaluations (
 namespace TEXT NOT NULL CHECK(namespace IN ('PRODUCTION','SHADOW','REPLAY','BACKTEST')),
 run_id TEXT NOT NULL, evaluation_key TEXT NOT NULL, input_hash TEXT NOT NULL,
 evaluation_id TEXT NOT NULL UNIQUE,
 symbol TEXT NOT NULL, evaluation_time TEXT NOT NULL, rule_version TEXT NOT NULL,
 feature_snapshot_hash TEXT NOT NULL, supersedes_evaluation_id TEXT,
 payload TEXT NOT NULL,
 PRIMARY KEY(namespace,run_id,evaluation_key)
);
CREATE TABLE IF NOT EXISTS csr_v33_events (
 namespace TEXT NOT NULL, run_id TEXT NOT NULL, event_id TEXT NOT NULL,
 evaluation_key TEXT NOT NULL, chain_id TEXT, event_type TEXT NOT NULL,
 sequence_no INTEGER NOT NULL, parent_event_id TEXT, payload TEXT NOT NULL,
 PRIMARY KEY(namespace,run_id,event_id),
 UNIQUE(namespace,run_id,evaluation_key,event_type,sequence_no),
 FOREIGN KEY(namespace,run_id,evaluation_key) REFERENCES csr_v33_evaluations(namespace,run_id,evaluation_key)
);
CREATE INDEX IF NOT EXISTS csr_v33_chain ON csr_v33_events(namespace,run_id,chain_id,sequence_no);
-- POSTGRES_ONLY
CREATE OR REPLACE FUNCTION csr_v33_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'V3.3 research history is immutable'; END;
$$;
DROP TRIGGER IF EXISTS csr_v33_feature_immutable ON csr_v33_features;
CREATE TRIGGER csr_v33_feature_immutable BEFORE UPDATE OR DELETE ON csr_v33_features FOR EACH ROW EXECUTE FUNCTION csr_v33_immutable();
DROP TRIGGER IF EXISTS csr_v33_evaluation_immutable ON csr_v33_evaluations;
CREATE TRIGGER csr_v33_evaluation_immutable BEFORE UPDATE OR DELETE ON csr_v33_evaluations FOR EACH ROW EXECUTE FUNCTION csr_v33_immutable();
DROP TRIGGER IF EXISTS csr_v33_event_immutable ON csr_v33_events;
CREATE TRIGGER csr_v33_event_immutable BEFORE UPDATE OR DELETE ON csr_v33_events FOR EACH ROW EXECUTE FUNCTION csr_v33_immutable();
-- New runtime privileges: SELECT,INSERT on features/evaluations/events;
-- SELECT,INSERT,UPDATE(revision,last_time,payload) on heads. No DELETE or DDL.
