-- Durable anonymous admission accounting without retaining clear client addresses.
ALTER TABLE upload_sessions ADD COLUMN admission_identity_hmac TEXT
CHECK(admission_identity_hmac IS NULL OR length(admission_identity_hmac) BETWEEN 16 AND 256);
ALTER TABLE jobs ADD COLUMN editable_relaunch_session_id INTEGER
REFERENCES upload_sessions(id) ON DELETE SET NULL;
-- Empty sessions created before two-tier admission have neither a trustworthy
-- identity nor the new short lifetime. Expire them during upgrade so they
-- cannot hold the pending quota for the legacy 24-hour retention window.
UPDATE upload_sessions
SET status='expired', reserved_bytes=0, updated_at=datetime('now')
WHERE status='created' AND reserved_bytes=0
AND NOT EXISTS (
    SELECT 1 FROM upload_files WHERE upload_files.session_id=upload_sessions.id
);
CREATE INDEX idx_upload_sessions_admission
ON upload_sessions(status, admission_identity_hmac, expires_at);
