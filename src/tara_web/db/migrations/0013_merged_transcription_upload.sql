ALTER TABLE upload_sessions ADD COLUMN input_type TEXT NOT NULL DEFAULT 'audio'
 CHECK(input_type IN ('audio','merged_transcription'));
ALTER TABLE upload_files ADD COLUMN schema_name TEXT
 CHECK(schema_name IS NULL OR length(schema_name) BETWEEN 1 AND 128);
ALTER TABLE upload_files ADD COLUMN schema_version TEXT
 CHECK(schema_version IS NULL OR length(schema_version) BETWEEN 1 AND 64);
ALTER TABLE upload_files ADD COLUMN token_count INTEGER
 CHECK(token_count IS NULL OR token_count >= 0);
ALTER TABLE upload_files ADD COLUMN validation_error_path TEXT
 CHECK(validation_error_path IS NULL OR length(validation_error_path) <= 256);

CREATE TABLE job_input_preparations_v13 (
 id INTEGER PRIMARY KEY,
 job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 upload_file_id INTEGER NOT NULL REFERENCES upload_files(id) ON DELETE RESTRICT,
 destination_path TEXT NOT NULL UNIQUE CHECK(
   length(destination_path) BETWEEN 24 AND 512
   AND (
     destination_path LIKE 'jobs/%/inputs/%.bin'
     OR destination_path LIKE 'jobs/%/inputs/%.yaml'
   )
   AND instr(destination_path, '..') = 0
   AND instr(destination_path, '\') = 0
 ),
 expected_bytes INTEGER NOT NULL CHECK(
   expected_bytes >= 0 AND expected_bytes <= 1073741824
 ),
 sha256_hex TEXT NOT NULL CHECK(length(sha256_hex) = 64),
 state TEXT NOT NULL CHECK(state IN ('prepared','moved')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(job_id, upload_file_id)
);
INSERT INTO job_input_preparations_v13
 SELECT * FROM job_input_preparations;
DROP TABLE job_input_preparations;
ALTER TABLE job_input_preparations_v13 RENAME TO job_input_preparations;
CREATE INDEX idx_job_input_preparations_recovery
 ON job_input_preparations(job_id, state, id);
