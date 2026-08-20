from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import migrate
from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.services.audio_probe import AudioProbeResult
from tara_web.services.zip_archive_validation import (
    ZipArchiveValidationRunner,
    ZipValidationPolicy,
)
from tara_web.services.zip_validation import ZipPolicy
from tara_web.storage.layout import StorageLayout


def test_runner_replaces_archive_with_validated_track_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    database = ConnectionFactory(storage / "web.sqlite3", storage)
    connection = database.connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    repository = AudioUploadRepository(database)
    layout = StorageLayout(storage)
    session_id = "us_zip_validation_000001"
    file_id = "uf_zip_validation_000001"
    repository.create_session(
        session_id, "v1:" + "a" * 64, max_sessions=10, input_type="zip"
    )
    archive_path = tmp_path / "tracks.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("table/Alice.mp3", b"audio-a")
        archive.writestr("MJ.ogg", b"audio-b")
        archive.writestr("notes.txt", b"ignored")
    content = archive_path.read_bytes()
    managed = layout.upload_file(session_id, file_id)
    repository.create_file(
        session_id=session_id,
        public_id=file_id,
        path=managed.relative_path,
        name="tracks.zip",
        person=None,
        size=len(content),
        digest=hashlib.sha256(content).hexdigest(),
        mime="application/zip",
        chunk_size=16_384,
        max_files=10,
        max_reserved_bytes=10_000_000,
    )
    layout.upload_path(managed.relative_path).write_bytes(content)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE upload_files SET confirmed_offset=? WHERE public_id=?",
            (len(content), file_id),
        )
    validation_id = "uv_zip_validation_000001"
    repository.queue_validation(session_id, file_id, 1, validation_id)
    row = repository.queued_validations(limit=1)[0]

    def probe(path: Path, **_: object) -> AudioProbeResult:
        kind = "mp3" if path.read_bytes() == b"audio-a" else "ogg"
        return AudioProbeResult(1_000, kind, None)

    monkeypatch.setattr(
        "tara_web.services.zip_archive_validation.validate_audio", probe
    )
    ZipArchiveValidationRunner(
        repository,
        layout,
        ZipValidationPolicy(
            archive=ZipPolicy(max_compression_ratio=1_000),
            ffprobe_timeout=1,
            ffmpeg_timeout=1,
        ),
    ).run(row)

    session = repository.session(session_id)
    files = repository.files_for_session(session_id, limit=10)
    assert session is not None and session["status"] == "ready"
    assert session["archive_phase"] == "launch_preparation"
    assert session["archive_excluded_count"] == 1
    assert {item["display_name"] for item in files} == {"Alice.mp3", "MJ.ogg"}
    assert {item["archive_entry_name"] for item in files} == {
        "table/Alice.mp3",
        "MJ.ogg",
    }
    assert {item["person"] for item in files} == {"Alice", "MJ"}
    assert all(item["status"] == "ready" for item in files)
    assert not layout.upload_path(managed.relative_path).exists()
