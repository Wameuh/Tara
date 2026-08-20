-- Durable tombstone progress for bounded upload filesystem cleanup.
ALTER TABLE upload_files ADD COLUMN storage_cleaned_at TEXT;
CREATE INDEX idx_upload_files_cleanup ON upload_files(storage_cleaned_at, status, id);
