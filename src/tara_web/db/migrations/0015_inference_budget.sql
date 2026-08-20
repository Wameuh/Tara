CREATE TABLE inference_budget (
 id INTEGER PRIMARY KEY CHECK(id=1),
 ceiling_micro_eur INTEGER NOT NULL CHECK(ceiling_micro_eur >= 0),
 spent_micro_eur INTEGER NOT NULL DEFAULT 0 CHECK(spent_micro_eur >= 0),
 reserved_micro_eur INTEGER NOT NULL DEFAULT 0 CHECK(reserved_micro_eur >= 0),
 updated_at TEXT NOT NULL,
 CHECK(spent_micro_eur + reserved_micro_eur >= 0)
);
CREATE TABLE inference_budget_reservations (
 id INTEGER PRIMARY KEY,
 job_public_id TEXT NOT NULL CHECK(length(job_public_id) BETWEEN 16 AND 128),
 attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
 reserved_micro_eur INTEGER NOT NULL CHECK(reserved_micro_eur >= 0),
 charged_micro_eur INTEGER CHECK(charged_micro_eur IS NULL OR charged_micro_eur >= 0),
 state TEXT NOT NULL CHECK(state IN ('reserved','reconciled')),
 created_at TEXT NOT NULL,
 reconciled_at TEXT,
 UNIQUE(job_public_id,attempt_number)
);
ALTER TABLE provider_circuits ADD COLUMN probe_started_at TEXT;
CREATE INDEX idx_budget_reservations_state
 ON inference_budget_reservations(state,created_at,id);
CREATE TABLE operator_action_audit (
 id INTEGER PRIMARY KEY,
 action TEXT NOT NULL CHECK(length(action) BETWEEN 1 AND 64),
 target TEXT NOT NULL CHECK(length(target) BETWEEN 1 AND 256),
 created_at TEXT NOT NULL
);
