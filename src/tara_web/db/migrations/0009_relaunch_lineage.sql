-- A relaunch gets a distinct upload session while preserving its parent job.
ALTER TABLE upload_sessions ADD COLUMN relaunch_parent_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL;
CREATE INDEX idx_upload_sessions_relaunch_parent ON upload_sessions(relaunch_parent_job_id, id);
