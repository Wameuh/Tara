-- All timestamps are UTC RFC3339 text. Monetary micro-euro amounts are INTEGER.
CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL CHECK (version >= 0));
CREATE TABLE upload_sessions (
 id INTEGER PRIMARY KEY, public_id TEXT NOT NULL UNIQUE CHECK(length(public_id) BETWEEN 16 AND 128),
 secret_hmac TEXT NOT NULL CHECK(length(secret_hmac) BETWEEN 16 AND 256), secret_generation INTEGER NOT NULL DEFAULT 1 CHECK(secret_generation > 0),
 status TEXT NOT NULL CHECK(status IN ('created','uploading','validating','ready','waiting_for_capacity','consumed','cancelled','expired')),
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0), reserved_bytes INTEGER NOT NULL DEFAULT 0 CHECK(reserved_bytes >= 0),
 expires_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, consumed_at TEXT
);
CREATE TABLE upload_files (
 id INTEGER PRIMARY KEY, public_id TEXT NOT NULL UNIQUE CHECK(length(public_id) BETWEEN 16 AND 128), session_id INTEGER NOT NULL REFERENCES upload_sessions(id) ON DELETE CASCADE,
 status TEXT NOT NULL CHECK(status IN ('created','uploading','finalizing','verifying','ready','invalid','replaced','deleted')),
 storage_path TEXT NOT NULL UNIQUE CHECK(length(storage_path) BETWEEN 1 AND 512 AND storage_path NOT LIKE '/%' AND instr(storage_path, '..') = 0),
 original_filename TEXT CHECK(length(original_filename) <= 255), declared_bytes INTEGER NOT NULL CHECK(declared_bytes >= 0), confirmed_offset INTEGER NOT NULL DEFAULT 0 CHECK(confirmed_offset >= 0 AND confirmed_offset <= declared_bytes),
 sha256_hex TEXT CHECK(sha256_hex IS NULL OR length(sha256_hex) = 64), revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE upload_chunks (id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES upload_files(id) ON DELETE CASCADE, offset_bytes INTEGER NOT NULL CHECK(offset_bytes >= 0), byte_count INTEGER NOT NULL CHECK(byte_count > 0), sha256_hex TEXT NOT NULL CHECK(length(sha256_hex) = 64), created_at TEXT NOT NULL, UNIQUE(file_id, offset_bytes));
CREATE TABLE jobs (
 id INTEGER PRIMARY KEY, public_id TEXT NOT NULL UNIQUE CHECK(length(public_id) BETWEEN 16 AND 128), upload_session_id INTEGER NOT NULL UNIQUE REFERENCES upload_sessions(id),
 secret_hmac TEXT NOT NULL CHECK(length(secret_hmac) BETWEEN 16 AND 256), secret_generation INTEGER NOT NULL DEFAULT 1 CHECK(secret_generation > 0),
 status TEXT NOT NULL CHECK(status IN ('queued','running','cancel_requested','stopping','completed','failed','timed_out','cancelled','cancel_failed','expired','deleted')),
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0), error_code TEXT CHECK(length(error_code) <= 64), error_message_key TEXT CHECK(length(error_message_key) <= 128),
 pipeline_version TEXT NOT NULL CHECK(length(pipeline_version) BETWEEN 1 AND 128), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE TABLE job_attempts (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, attempt_number INTEGER NOT NULL CHECK(attempt_number > 0), status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')), error_code TEXT CHECK(length(error_code) <= 64), started_at TEXT, finished_at TEXT, duration_ms INTEGER CHECK(duration_ms >= 0), provider_cost_micro_eur INTEGER NOT NULL DEFAULT 0 CHECK(provider_cost_micro_eur >= 0), input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0), output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0), UNIQUE(job_id, attempt_number));
CREATE TABLE job_artifacts (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, artifact_type TEXT NOT NULL CHECK(artifact_type IN ('audio_input','merged_transcription_input','context_input','previous_summary_input','raw_transcription','scene_data','blackboard','prompt','cache','final_yaml','technical_error')), retention_kind TEXT NOT NULL CHECK(retention_kind IN ('intermediate','final_result')), storage_state TEXT NOT NULL CHECK(storage_state IN ('pending','ready','deleting','deleted','error')), relative_path TEXT NOT NULL CHECK(length(relative_path) BETWEEN 1 AND 512 AND relative_path NOT LIKE '/%' AND instr(relative_path, '..') = 0), original_filename TEXT CHECK(length(original_filename) <= 255), sha256_hex TEXT CHECK(sha256_hex IS NULL OR length(sha256_hex) = 64), storage_error_code TEXT CHECK(storage_error_code IN ('write_failed','integrity_failed','delete_failed')), expires_at TEXT NOT NULL, deleted_at TEXT, created_at TEXT NOT NULL, UNIQUE(job_id, artifact_type, relative_path));
CREATE TABLE idempotency_keys (id INTEGER PRIMARY KEY, operation TEXT NOT NULL CHECK(length(operation) BETWEEN 1 AND 64), owner_hmac TEXT NOT NULL CHECK(length(owner_hmac) BETWEEN 16 AND 256), key_hmac TEXT NOT NULL CHECK(length(key_hmac) BETWEEN 16 AND 256), request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64), result_json TEXT CHECK(length(result_json) <= 65536), created_at TEXT NOT NULL, completed_at TEXT, expires_at TEXT NOT NULL, UNIQUE(operation, owner_hmac, key_hmac));
CREATE TABLE provider_circuits (provider TEXT NOT NULL CHECK(length(provider) BETWEEN 1 AND 64), operation_family TEXT NOT NULL CHECK(length(operation_family) BETWEEN 1 AND 64), state TEXT NOT NULL CHECK(state IN ('closed','open','half_open')), failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0), open_until TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(provider, operation_family));
CREATE TABLE job_metrics (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE, job_type TEXT NOT NULL CHECK(length(job_type) BETWEEN 1 AND 64), pipeline_version TEXT NOT NULL CHECK(length(pipeline_version) BETWEEN 1 AND 128), completed_at TEXT NOT NULL, duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0), provider_cost_micro_eur INTEGER NOT NULL DEFAULT 0 CHECK(provider_cost_micro_eur >= 0), attempt_count INTEGER NOT NULL CHECK(attempt_count >= 1), failed_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempt_count >= 0), cancelled_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(cancelled_attempt_count >= 0));
CREATE TABLE job_failure_metrics (date TEXT NOT NULL CHECK(length(date) = 10), error_code TEXT NOT NULL CHECK(length(error_code) BETWEEN 1 AND 64), count INTEGER NOT NULL CHECK(count >= 0), PRIMARY KEY(date, error_code));
CREATE TABLE pre_job_error_metrics (date TEXT NOT NULL CHECK(length(date) = 10), error_code TEXT NOT NULL CHECK(length(error_code) BETWEEN 1 AND 64), input_type TEXT NOT NULL CHECK(input_type IN ('audio','merged_transcription','context','previous_summaries','request')), count INTEGER NOT NULL CHECK(count >= 0), PRIMARY KEY(date, error_code, input_type));
CREATE INDEX idx_upload_sessions_expiry ON upload_sessions(expires_at, status);
CREATE INDEX idx_upload_files_session ON upload_files(session_id, status);
CREATE INDEX idx_upload_chunks_file_offset ON upload_chunks(file_id, offset_bytes);
CREATE INDEX idx_jobs_queue ON jobs(status, created_at);
CREATE INDEX idx_job_attempts_job ON job_attempts(job_id, attempt_number);
CREATE INDEX idx_artifacts_expiry ON job_artifacts(expires_at, storage_state);
CREATE INDEX idx_artifacts_job_type ON job_artifacts(job_id, artifact_type);
CREATE INDEX idx_idempotency_expiry ON idempotency_keys(expires_at);
CREATE INDEX idx_job_metrics_lookup ON job_metrics(job_type, pipeline_version, completed_at);
