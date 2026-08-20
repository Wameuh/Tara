-- Durable claim and immutable worker request for the local orchestrator.
CREATE TABLE job_run_snapshots (
 id INTEGER PRIMARY KEY,
 job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
 request_json TEXT NOT NULL CHECK(length(request_json) BETWEEN 2 AND 32768),
 claimed_at TEXT NOT NULL,
 worker_token TEXT NOT NULL UNIQUE CHECK(length(worker_token) BETWEEN 32 AND 128),
 last_event_revision INTEGER NOT NULL DEFAULT -1 CHECK(last_event_revision >= -1),
 UNIQUE(job_id, attempt_number)
);
CREATE TABLE job_run_events (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 attempt_number INTEGER NOT NULL, event_revision INTEGER NOT NULL, event_type TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(length(payload_json) <= 16384), created_at TEXT NOT NULL,
 UNIQUE(job_id, attempt_number, event_revision)
);
CREATE INDEX idx_jobs_fifo_claim ON jobs(status, created_at, id);
CREATE INDEX idx_job_run_snapshots_claim ON job_run_snapshots(job_id, attempt_number);
CREATE TABLE job_input_preparations (
 id INTEGER PRIMARY KEY,
 job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 upload_file_id INTEGER NOT NULL REFERENCES upload_files(id) ON DELETE RESTRICT,
 destination_path TEXT NOT NULL UNIQUE CHECK(length(destination_path) BETWEEN 24 AND 512 AND destination_path LIKE 'jobs/%/inputs/%.bin' AND instr(destination_path, '..') = 0 AND instr(destination_path, '\\') = 0),
 expected_bytes INTEGER NOT NULL CHECK(expected_bytes >= 0 AND expected_bytes <= 1073741824),
 sha256_hex TEXT NOT NULL CHECK(length(sha256_hex) = 64),
 state TEXT NOT NULL CHECK(state IN ('prepared','moved')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(job_id, upload_file_id)
);
CREATE INDEX idx_job_input_preparations_recovery ON job_input_preparations(job_id, state, id);
CREATE INDEX idx_upload_files_job_ready_active ON upload_files(job_id, status, active, id);
