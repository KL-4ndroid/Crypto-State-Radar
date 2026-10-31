-- Explicit additive migration, separate from model/evaluation tables.
CREATE TABLE IF NOT EXISTS csr_v33_acquisition (
 namespace TEXT NOT NULL CHECK(namespace='SHADOW'), run_id TEXT NOT NULL,
 source TEXT NOT NULL, symbol TEXT NOT NULL, revision INTEGER NOT NULL,
 cursor_payload TEXT NOT NULL, next_due TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 lease_token TEXT, lease_until TEXT, last_error TEXT,
 PRIMARY KEY(namespace,run_id,source,symbol)
);
CREATE TABLE IF NOT EXISTS csr_v33_acquisition_items (
 namespace TEXT NOT NULL, run_id TEXT NOT NULL, source TEXT NOT NULL, symbol TEXT NOT NULL,
 item_key TEXT NOT NULL, content_hash TEXT NOT NULL, effective_time TEXT NOT NULL,
 availability_time TEXT NOT NULL, record_id TEXT NOT NULL,
 PRIMARY KEY(namespace,run_id,source,symbol,item_key,content_hash)
);
CREATE INDEX IF NOT EXISTS csr_v33_acquisition_asof ON csr_v33_acquisition_items(namespace,run_id,source,symbol,effective_time,availability_time);
-- POSTGRES_ONLY
DROP TRIGGER IF EXISTS csr_v33_acquisition_items_immutable ON csr_v33_acquisition_items;
CREATE TRIGGER csr_v33_acquisition_items_immutable BEFORE UPDATE OR DELETE ON csr_v33_acquisition_items
FOR EACH ROW EXECUTE FUNCTION csr_v33_immutable();
-- Runtime: SELECT,INSERT on items; SELECT,INSERT and UPDATE of worker metadata
-- on acquisition. csr_records remains append-only. No DELETE or DDL.
