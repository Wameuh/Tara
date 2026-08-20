-- Direct resumable MP3/OGG upload metadata. ZIP is deliberately excluded.
ALTER TABLE upload_sessions ADD COLUMN cancelled_at TEXT;
ALTER TABLE upload_sessions ADD COLUMN last_activity_at TEXT;
ALTER TABLE upload_files ADD COLUMN person TEXT CHECK(person IS NULL OR length(person) BETWEEN 1 AND 255);
ALTER TABLE upload_files ADD COLUMN display_name TEXT CHECK(display_name IS NULL OR length(display_name) BETWEEN 1 AND 255);
ALTER TABLE upload_files ADD COLUMN declared_mime TEXT CHECK(declared_mime IS NULL OR length(declared_mime) <= 128);
ALTER TABLE upload_files ADD COLUMN detected_type TEXT CHECK(detected_type IS NULL OR detected_type IN ('mp3','ogg'));
ALTER TABLE upload_files ADD COLUMN duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0);
ALTER TABLE upload_files ADD COLUMN chunk_size INTEGER NOT NULL DEFAULT 1048576 CHECK(chunk_size BETWEEN 16384 AND 67108864);
ALTER TABLE upload_files ADD COLUMN validation_attempt INTEGER NOT NULL DEFAULT 0 CHECK(validation_attempt BETWEEN 0 AND 100);
ALTER TABLE upload_files ADD COLUMN validation_error_code TEXT CHECK(validation_error_code IS NULL OR length(validation_error_code) <= 64);
ALTER TABLE upload_files ADD COLUMN validation_warning_code TEXT CHECK(validation_warning_code IS NULL OR length(validation_warning_code) <= 64);
ALTER TABLE upload_files ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1));
ALTER TABLE upload_files ADD COLUMN replacement_for_id INTEGER REFERENCES upload_files(id) ON DELETE SET NULL;
CREATE TABLE upload_validations (
 id INTEGER PRIMARY KEY,
 public_id TEXT NOT NULL UNIQUE CHECK(length(public_id) BETWEEN 16 AND 128),
 file_id INTEGER NOT NULL REFERENCES upload_files(id) ON DELETE CASCADE,
 status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancel_requested','cancelled')),
 attempt INTEGER NOT NULL CHECK(attempt BETWEEN 1 AND 100),
 error_code TEXT CHECK(error_code IS NULL OR length(error_code) <= 64),
 warning_code TEXT CHECK(warning_code IS NULL OR length(warning_code) <= 64),
 queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
 UNIQUE(file_id, attempt)
);
CREATE INDEX idx_upload_files_session_order ON upload_files(session_id, id);
CREATE INDEX idx_upload_files_validation ON upload_files(status, updated_at, id);
CREATE INDEX idx_upload_validations_queue ON upload_validations(status, queued_at, id);
CREATE INDEX idx_upload_sessions_activity ON upload_sessions(status, last_activity_at, expires_at);
