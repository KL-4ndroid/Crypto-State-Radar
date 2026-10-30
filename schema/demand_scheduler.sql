-- Explicit additive migration. Does not change model state or research history.
CREATE TABLE IF NOT EXISTS csr_scheduler_wakes (
 operation TEXT PRIMARY KEY CHECK (operation IN ('drain','validate')),
 token TEXT NOT NULL, due_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('pending','sent','processing','idle')),
 lease_until TEXT
);
-- Existing runtime role needs SELECT, INSERT, UPDATE on this table only.
