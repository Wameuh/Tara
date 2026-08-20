ALTER TABLE job_artifacts ADD COLUMN byte_size INTEGER CHECK(byte_size IS NULL OR byte_size >= 0);
ALTER TABLE job_artifacts ADD COLUMN delete_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(delete_attempt_count >= 0);
ALTER TABLE job_artifacts ADD COLUMN last_delete_attempt_at TEXT;
ALTER TABLE job_artifacts ADD COLUMN updated_at TEXT;
CREATE INDEX idx_job_artifacts_reconcile ON job_artifacts(storage_state, expires_at);
CREATE TRIGGER job_artifacts_require_final_hash BEFORE UPDATE OF storage_state ON job_artifacts
WHEN NEW.storage_state = 'ready' AND NEW.artifact_type = 'final_yaml'
 AND (NEW.sha256_hex IS NULL OR length(NEW.sha256_hex) != 64)
BEGIN SELECT RAISE(ABORT, 'final artifact hash required'); END;
