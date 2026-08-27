from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from types import SimpleNamespace

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


def test_runner_preserves_precise_archive_rejection_reason(tmp_path: Path) -> None:
    archive_path = tmp_path / "tracks.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("recording.flac", b"audio")
        archive.writestr("notes.txt", b"notes")
    content = archive_path.read_bytes()
    finished: list[dict[str, object]] = []
    repository = SimpleNamespace(
        claim_validation=lambda _identifier: True,
        set_archive_phase=lambda *_args: None,
        finish_validation=lambda _identifier, **result: finished.append(result),
    )
    layout = SimpleNamespace(
        root=tmp_path,
        upload_path=lambda _relative: archive_path,
    )

    ZipArchiveValidationRunner(
        repository,
        layout,
        ZipValidationPolicy(
            archive=ZipPolicy(max_compression_ratio=1_000),
            ffprobe_timeout=1,
            ffmpeg_timeout=1,
        ),
    ).run(
        {
            "validation_id": "uv_zip_validation_000001",
            "storage_path": "uploads/session/archive.part",
            "declared_bytes": len(content),
            "sha256_hex": hashlib.sha256(content).hexdigest(),
        }
    )

    assert finished[0]["error_code"] == "zip_no_supported_audio"


def test_runner_applies_one_deadline_to_track_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "tracks.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("recording.mp3", b"audio")
    content = archive_path.read_bytes()
    finished: list[dict[str, object]] = []
    repository = SimpleNamespace(
        claim_validation=lambda _identifier: True,
        set_archive_phase=lambda *_args: None,
        validation_is_running=lambda _identifier: True,
        finish_validation=lambda _identifier, **result: finished.append(result),
    )

    def upload_path(relative: str) -> Path:
        return archive_path if relative == "archive.part" else tmp_path / relative

    layout = SimpleNamespace(
        root=tmp_path,
        upload_path=upload_path,
        upload_file=lambda _session, _file: SimpleNamespace(
            relative_path="validated-track.mp3"
        ),
    )
    monkeypatch.setattr(
        "tara_web.services.zip_archive_validation.validate_audio",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("aggregate deadline")
        ),
    )

    ZipArchiveValidationRunner(
        repository,
        layout,
        ZipValidationPolicy(
            archive=ZipPolicy(max_compression_ratio=1_000),
            ffprobe_timeout=30,
            ffmpeg_timeout=60,
        ),
    ).run(
        {
            "validation_id": "uv_zip_validation_000002",
            "session_public_id": "us_zip_validation_000002",
            "storage_path": "archive.part",
            "declared_bytes": len(content),
            "sha256_hex": hashlib.sha256(content).hexdigest(),
        }
    )

    assert finished[0]["error_code"] == "zip_timeout"
    assert not (tmp_path / "validated-track.mp3").exists()


def test_runner_replaces_archive_with_validated_track_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir(mode=0o700)
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
        archive.writestr("Guest.aac", b"audio-c")
        archive.writestr("Music.m4a", b"audio-d")
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
        kind = {
            b"audio-a": "mp3",
            b"audio-b": "ogg",
            b"audio-c": "aac",
            b"audio-d": "m4a",
        }[path.read_bytes()]
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
    assert {item["display_name"] for item in files} == {
        "Alice.mp3",
        "MJ.ogg",
        "Guest.aac",
        "Music.m4a",
    }
    assert {item["archive_entry_name"] for item in files} == {
        "table/Alice.mp3",
        "MJ.ogg",
        "Guest.aac",
        "Music.m4a",
    }
    assert {item["person"] for item in files} == {"Alice", "MJ", "Guest", "Music"}
    assert all(item["status"] == "ready" for item in files)
    assert not layout.upload_path(managed.relative_path).exists()
