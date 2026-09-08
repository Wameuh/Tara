ALTER TABLE provider_usage_attempts ADD COLUMN operation_name TEXT NOT NULL DEFAULT 'unspecified' CHECK(length(operation_name) BETWEEN 1 AND 64);
CREATE INDEX idx_provider_usage_operation ON provider_usage_attempts(operation_family,operation_name,created_at);
