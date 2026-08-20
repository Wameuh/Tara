-- Distinguish an observed zero cost from telemetry that was never reported.
ALTER TABLE job_attempts ADD COLUMN cost_known INTEGER NOT NULL DEFAULT 0
 CHECK(cost_known IN (0, 1));
