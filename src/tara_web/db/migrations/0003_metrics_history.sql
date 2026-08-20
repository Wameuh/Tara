-- Regression samples outlive operational job metadata; all amounts are micro-euros.
ALTER TABLE jobs ADD COLUMN language TEXT NOT NULL DEFAULT 'fr' CHECK(length(language) BETWEEN 2 AND 16);
CREATE TABLE job_metrics_v3 (
 sample_id INTEGER PRIMARY KEY,
 job_id INTEGER,
 job_public_id TEXT NOT NULL CHECK(length(job_public_id) BETWEEN 16 AND 128),
 job_type TEXT NOT NULL CHECK(job_type IN ('audio','merged_transcription')),
 pipeline_version TEXT NOT NULL CHECK(length(pipeline_version) BETWEEN 1 AND 128),
 created_at TEXT NOT NULL, completed_at TEXT NOT NULL,
 input_size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(input_size_bytes >= 0), input_file_count INTEGER NOT NULL DEFAULT 0 CHECK(input_file_count >= 0),
 merged_transcription_size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(merged_transcription_size_bytes >= 0),
 merged_transcription_tokens INTEGER NOT NULL DEFAULT 0 CHECK(merged_transcription_tokens >= 0),
 input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0), output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
 duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0), audio_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(audio_duration_ms >= 0), transcription_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(transcription_duration_ms >= 0),
 validation_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(validation_duration_ms >= 0), preparation_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(preparation_duration_ms >= 0), narrative_analysis_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(narrative_analysis_duration_ms >= 0), synthesis_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(synthesis_duration_ms >= 0), verification_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(verification_duration_ms >= 0),
 provider_cost_micro_eur INTEGER NOT NULL DEFAULT 0 CHECK(provider_cost_micro_eur >= 0), attempt_count INTEGER NOT NULL CHECK(attempt_count >= 1), failed_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempt_count >= 0), cancelled_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(cancelled_attempt_count >= 0),
 UNIQUE(job_public_id)
);
INSERT INTO job_metrics_v3(job_id,job_public_id,job_type,pipeline_version,created_at,completed_at,duration_ms,audio_duration_ms,transcription_duration_ms,merged_transcription_tokens,validation_duration_ms,preparation_duration_ms,narrative_analysis_duration_ms,synthesis_duration_ms,verification_duration_ms,provider_cost_micro_eur,attempt_count,failed_attempt_count,cancelled_attempt_count)
SELECT m.job_id,j.public_id,CASE WHEN m.job_type='zip' THEN 'audio' ELSE m.job_type END,m.pipeline_version,j.created_at,m.completed_at,m.duration_ms,m.audio_duration_ms,m.transcription_duration_ms,m.merged_transcription_tokens,m.validation_duration_ms,m.preparation_duration_ms,m.narrative_analysis_duration_ms,m.synthesis_duration_ms,m.verification_duration_ms,m.provider_cost_micro_eur,m.attempt_count,m.failed_attempt_count,m.cancelled_attempt_count FROM job_metrics m JOIN jobs j ON j.id=m.job_id;
DROP TABLE job_metrics;
ALTER TABLE job_metrics_v3 RENAME TO job_metrics;
CREATE TABLE job_failure_metrics_v3 (
 date TEXT NOT NULL CHECK(length(date)=10), final_status TEXT NOT NULL CHECK(final_status IN ('failed','timed_out','cancelled','cancel_failed')),
 job_count INTEGER NOT NULL DEFAULT 0 CHECK(job_count >= 0), failed_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempt_count >= 0),
 waiting_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(waiting_duration_ms >= 0), execution_duration_ms INTEGER NOT NULL DEFAULT 0 CHECK(execution_duration_ms >= 0),
 estimated_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(estimated_input_tokens >= 0), estimated_output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(estimated_output_tokens >= 0), actual_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(actual_input_tokens >= 0), actual_output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(actual_output_tokens >= 0), estimated_cost_micro_eur INTEGER NOT NULL DEFAULT 0 CHECK(estimated_cost_micro_eur >= 0), actual_cost_micro_eur INTEGER NOT NULL DEFAULT 0 CHECK(actual_cost_micro_eur >= 0), PRIMARY KEY(date,final_status)
);
INSERT INTO job_failure_metrics_v3(date,final_status,job_count,failed_attempt_count,execution_duration_ms,actual_input_tokens,actual_output_tokens,actual_cost_micro_eur)
SELECT date,outcome_type,SUM(count),SUM(attempt_count),SUM(duration_ms),SUM(input_tokens),SUM(output_tokens),SUM(provider_cost_micro_eur) FROM job_failure_metrics GROUP BY date,outcome_type;
DROP TABLE job_failure_metrics;
ALTER TABLE job_failure_metrics_v3 RENAME TO job_failure_metrics;
CREATE INDEX idx_job_metrics_history ON job_metrics(job_type,pipeline_version,completed_at);
UPDATE jobs SET job_type = 'audio' WHERE job_type = 'zip';
UPDATE jobs SET expires_at = updated_at WHERE expires_at IS NULL OR expires_at = '';
CREATE TRIGGER jobs_require_expires_at BEFORE INSERT ON jobs WHEN NEW.expires_at IS NULL OR NEW.expires_at = '' OR (NEW.expires_at NOT LIKE '%Z' AND NEW.expires_at NOT LIKE '%+00:00') BEGIN SELECT RAISE(ABORT, 'job expiration required'); END;
CREATE TRIGGER jobs_forbid_zip BEFORE INSERT ON jobs WHEN NEW.job_type = 'zip' BEGIN SELECT RAISE(ABORT, 'job type invalid'); END;
CREATE TRIGGER jobs_require_expires_at_update BEFORE UPDATE OF expires_at ON jobs WHEN NEW.expires_at IS NULL OR NEW.expires_at = '' OR (NEW.expires_at NOT LIKE '%Z' AND NEW.expires_at NOT LIKE '%+00:00') BEGIN SELECT RAISE(ABORT, 'job expiration required'); END;
CREATE TRIGGER jobs_forbid_zip_update BEFORE UPDATE OF job_type ON jobs WHEN NEW.job_type = 'zip' BEGIN SELECT RAISE(ABORT, 'job type invalid'); END;
