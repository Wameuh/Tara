-- A staged record proves a fsynced temporary or final file after a crash.
CREATE TABLE job_artifacts_v5 (
 id INTEGER PRIMARY KEY,
 job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 artifact_type TEXT NOT NULL CHECK(artifact_type IN ('audio_input','merged_transcription_input','context_input','previous_summary_input','raw_transcription','scene_data','blackboard','prompt','cache','final_yaml','technical_error')),
 retention_kind TEXT NOT NULL CHECK(retention_kind IN ('intermediate','final_result')),
 storage_state TEXT NOT NULL CHECK(storage_state IN ('pending','staged','ready','deleting','deleted','error')),
 relative_path TEXT NOT NULL CHECK(length(relative_path) BETWEEN 1 AND 512),
 original_filename TEXT CHECK(length(original_filename) <= 255),
 sha256_hex TEXT CHECK(sha256_hex IS NULL OR (length(sha256_hex) = 64 AND sha256_hex NOT GLOB '*[^0-9a-f]*')),
 byte_size INTEGER CHECK(byte_size IS NULL OR byte_size >= 0),
 staged_temp_name TEXT CHECK(staged_temp_name IS NULL OR length(staged_temp_name) BETWEEN 1 AND 128),
 storage_error_code TEXT CHECK(storage_error_code IN ('write_failed','integrity_failed','delete_failed')),
 expires_at TEXT NOT NULL,
 deleted_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 delete_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(delete_attempt_count >= 0),
 last_delete_attempt_at TEXT,
 UNIQUE(job_id, artifact_type, relative_path)
);
INSERT INTO job_artifacts_v5(id,job_id,artifact_type,retention_kind,storage_state,relative_path,original_filename,sha256_hex,byte_size,storage_error_code,expires_at,deleted_at,created_at,updated_at,delete_attempt_count,last_delete_attempt_at)
SELECT id,job_id,artifact_type,retention_kind,storage_state,relative_path,original_filename,sha256_hex,byte_size,storage_error_code,expires_at,deleted_at,created_at,COALESCE(updated_at,created_at),delete_attempt_count,last_delete_attempt_at FROM job_artifacts;
DROP TABLE job_artifacts;
ALTER TABLE job_artifacts_v5 RENAME TO job_artifacts;
CREATE INDEX idx_job_artifacts_reconcile ON job_artifacts(storage_state, expires_at, id);
CREATE INDEX idx_job_artifacts_job ON job_artifacts(job_id, storage_state, id);
CREATE TRIGGER job_artifacts_state_invariants_insert BEFORE INSERT ON job_artifacts
WHEN (NEW.storage_state = 'staged' AND (NEW.byte_size IS NULL OR NEW.staged_temp_name IS NULL))
 OR (NEW.storage_state = 'staged' AND NEW.artifact_type = 'final_yaml' AND NEW.sha256_hex IS NULL)
 OR (NEW.storage_state = 'ready' AND (NEW.byte_size IS NULL OR NEW.staged_temp_name IS NOT NULL))
 OR (NEW.storage_state = 'ready' AND NEW.artifact_type = 'final_yaml' AND NEW.sha256_hex IS NULL)
 OR (NEW.storage_state = 'deleted' AND (NEW.original_filename IS NOT NULL OR NEW.deleted_at IS NULL OR NEW.staged_temp_name IS NOT NULL))
 OR ((NEW.artifact_type = 'final_yaml') != (NEW.retention_kind = 'final_result'))
BEGIN SELECT RAISE(ABORT, 'artifact state invariant'); END;
CREATE TRIGGER job_artifacts_state_invariants_update BEFORE UPDATE ON job_artifacts
WHEN (NEW.storage_state = 'staged' AND (NEW.byte_size IS NULL OR NEW.staged_temp_name IS NULL))
 OR (NEW.storage_state = 'staged' AND NEW.artifact_type = 'final_yaml' AND NEW.sha256_hex IS NULL)
 OR (NEW.storage_state = 'ready' AND (NEW.byte_size IS NULL OR NEW.staged_temp_name IS NOT NULL))
 OR (NEW.storage_state = 'ready' AND NEW.artifact_type = 'final_yaml' AND NEW.sha256_hex IS NULL)
 OR (NEW.storage_state = 'deleted' AND (NEW.original_filename IS NOT NULL OR NEW.deleted_at IS NULL OR NEW.staged_temp_name IS NOT NULL))
 OR ((NEW.artifact_type = 'final_yaml') != (NEW.retention_kind = 'final_result'))
BEGIN SELECT RAISE(ABORT, 'artifact state invariant'); END;
