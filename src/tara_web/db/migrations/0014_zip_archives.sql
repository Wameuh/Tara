ALTER TABLE upload_sessions ADD COLUMN archive_mode INTEGER NOT NULL DEFAULT 0
 CHECK(archive_mode IN (0,1));
ALTER TABLE upload_sessions ADD COLUMN archive_phase TEXT
 CHECK(archive_phase IS NULL OR archive_phase IN
 ('transfer','extraction','track_validation','launch_preparation'));
ALTER TABLE upload_files ADD COLUMN archive_parent_id INTEGER
 REFERENCES upload_files(id) ON DELETE SET NULL;
ALTER TABLE upload_files ADD COLUMN archive_entry_name TEXT
 CHECK(archive_entry_name IS NULL OR length(archive_entry_name) BETWEEN 1 AND 1024);
ALTER TABLE upload_files ADD COLUMN archive_excluded_count INTEGER
 CHECK(archive_excluded_count IS NULL OR archive_excluded_count >= 0);
CREATE INDEX idx_upload_files_archive_parent
 ON upload_files(archive_parent_id,id);
