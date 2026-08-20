-- A timed-out job can produce at most one distinct identical child job.
ALTER TABLE jobs ADD COLUMN identical_relaunch_job_id INTEGER
 REFERENCES jobs(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX idx_jobs_identical_relaunch_child
 ON jobs(identical_relaunch_job_id)
 WHERE identical_relaunch_job_id IS NOT NULL;
