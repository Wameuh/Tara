-- Durable operational snapshot. Values are bounded; public text is never stored here.
ALTER TABLE upload_files ADD COLUMN job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL;
ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'audio' CHECK(job_type IN ('audio','merged_transcription','zip'));
ALTER TABLE jobs ADD COLUMN root_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL;
ALTER TABLE jobs ADD COLUMN parent_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL;
ALTER TABLE jobs ADD COLUMN current_attempt_number INTEGER NOT NULL DEFAULT 1 CHECK(current_attempt_number > 0);
ALTER TABLE jobs ADD COLUMN expires_at TEXT;
ALTER TABLE jobs ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued' CHECK(stage IN ('queued','input_validation','transcription','session_preparation','narrative_analysis','synthesis','verification','result_ready'));
ALTER TABLE jobs ADD COLUMN substage TEXT CHECK(substage IS NULL OR length(substage) <= 64);
ALTER TABLE jobs ADD COLUMN stage_progress_milli INTEGER NOT NULL DEFAULT 0 CHECK(stage_progress_milli BETWEEN 0 AND 1000);
ALTER TABLE jobs ADD COLUMN total_progress_milli INTEGER NOT NULL DEFAULT 0 CHECK(total_progress_milli BETWEEN 0 AND 1000);
ALTER TABLE jobs ADD COLUMN queue_position INTEGER CHECK(queue_position IS NULL OR queue_position >= 0);
ALTER TABLE jobs ADD COLUMN estimated_wait_ms INTEGER CHECK(estimated_wait_ms IS NULL OR estimated_wait_ms >= 0);
ALTER TABLE jobs ADD COLUMN estimated_remaining_ms INTEGER CHECK(estimated_remaining_ms IS NULL OR estimated_remaining_ms >= 0);
ALTER TABLE jobs ADD COLUMN input_file_count INTEGER NOT NULL DEFAULT 0 CHECK(input_file_count >= 0 AND input_file_count <= 10000);
ALTER TABLE jobs ADD COLUMN snapshot_version INTEGER NOT NULL DEFAULT 1 CHECK(snapshot_version = 1);
ALTER TABLE jobs ADD COLUMN allowed_actions_json TEXT NOT NULL DEFAULT '[]' CHECK(length(allowed_actions_json) <= 4096);
ALTER TABLE job_metrics ADD COLUMN audio_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(audio_duration_ms >= 0);
ALTER TABLE job_metrics ADD COLUMN transcription_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(transcription_duration_ms >= 0);
ALTER TABLE job_metrics ADD COLUMN merged_transcription_tokens INTEGER NOT NULL DEFAULT 0 CHECK(merged_transcription_tokens >= 0);
ALTER TABLE job_metrics ADD COLUMN validation_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(validation_duration_ms >= 0);
ALTER TABLE job_metrics ADD COLUMN preparation_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(preparation_duration_ms >= 0);
ALTER TABLE job_metrics ADD COLUMN narrative_analysis_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(narrative_analysis_duration_ms >= 0);
ALTER TABLE job_metrics ADD COLUMN synthesis_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(synthesis_duration_ms >= 0);
ALTER TABLE job_metrics ADD COLUMN verification_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(verification_duration_ms >= 0);
CREATE TABLE job_failure_metrics_v2 (
 date TEXT NOT NULL CHECK(length(date) = 10), error_code TEXT NOT NULL CHECK(length(error_code) BETWEEN 1 AND 64),
 outcome_type TEXT NOT NULL CHECK(outcome_type IN ('failed','timed_out','cancelled','cancel_failed')),
 count INTEGER NOT NULL CHECK(count >= 0), attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
 duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(duration_ms >= 0), input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
 output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0), provider_cost_micro_eur INTEGER NOT NULL DEFAULT 0 CHECK(provider_cost_micro_eur >= 0),
 PRIMARY KEY(date, error_code, outcome_type)
);
INSERT INTO job_failure_metrics_v2(date, error_code, outcome_type, count) SELECT date, error_code, 'failed', count FROM job_failure_metrics;
DROP TABLE job_failure_metrics;
ALTER TABLE job_failure_metrics_v2 RENAME TO job_failure_metrics;
CREATE INDEX idx_upload_files_job ON upload_files(job_id, status);
CREATE INDEX idx_jobs_read_snapshot ON jobs(status, created_at, revision);
CREATE INDEX idx_jobs_expiration ON jobs(expires_at, status);
CREATE INDEX idx_jobs_lineage ON jobs(root_job_id, parent_job_id, created_at);
