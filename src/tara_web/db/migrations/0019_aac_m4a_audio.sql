ALTER TABLE upload_files RENAME COLUMN detected_type TO detected_type_legacy;
ALTER TABLE upload_files ADD COLUMN detected_type TEXT
 CHECK(detected_type IS NULL OR detected_type IN ('mp3','ogg','aac','m4a'));
UPDATE upload_files SET detected_type=detected_type_legacy;
ALTER TABLE upload_files DROP COLUMN detected_type_legacy;
