-- Durable acknowledgement that terminal-job private inputs and work products
-- have been physically removed. NULL is intentionally retryable after a crash.
ALTER TABLE jobs ADD COLUMN private_artifacts_cleaned_at TEXT;
CREATE INDEX idx_jobs_terminal_private_cleanup
ON jobs(status, private_artifacts_cleaned_at, id);
