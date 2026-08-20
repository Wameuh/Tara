from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tara.schemas.merged_transcription import new_merged_transcription
from tara.yaml_utils import to_yaml
from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import migrate
from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.services.merged_transcription_validation import (
    MergedTranscriptionValidationRunner,
    MergedValidationPolicy,
)
from tara_web.storage.layout import StorageLayout


def _validate(
    tmp_path: Path, content: bytes, *, max_tokens: int = 500_000
) -> dict[str, object]:
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
    session_id = "us_merged_validation_0001"
    file_id = "uf_merged_validation_0001"
    repository.create_session(
        session_id,
        "v1:" + "a" * 64,
        max_sessions=10,
        input_type="merged_transcription",
    )
    managed = layout.upload_file(session_id, file_id)
    repository.create_file(
        session_id=session_id,
        public_id=file_id,
        path=managed.relative_path,
        name="transcription.yaml",
        person=None,
        size=len(content),
        digest=hashlib.sha256(content).hexdigest(),
        mime="application/yaml",
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
    validation_id = "uv_merged_validation_0001"
    repository.queue_validation(session_id, file_id, 1, validation_id)
    row = repository.queued_validations(limit=1)[0]
    MergedTranscriptionValidationRunner(
        repository,
        layout,
        MergedValidationPolicy(
            max_bytes=32 * 1024 * 1024,
            max_tokens=max_tokens,
            timeout_seconds=15,
            memory_bytes=512 * 1024 * 1024,
        ),
    ).run(row)
    result = repository.file_for_session(session_id, file_id)
    assert result is not None
    return result


def _current_yaml(text: str = "Une transcription valide.") -> bytes:
    merged = new_merged_transcription(text=text, segments=[])
    return to_yaml(merged.to_dict()).encode("utf-8")


def test_valid_yaml_persists_version_and_canonical_token_count(tmp_path: Path) -> None:
    row = _validate(tmp_path, _current_yaml())
    assert row["status"] == "ready"
    assert row["schema_name"] == "tara.merged_transcription"
    assert row["schema_version"] == "26.0.1"
    assert isinstance(row["token_count"], int) and row["token_count"] > 0
    assert row["validation_error_code"] is None


@pytest.mark.parametrize(
    "content",
    [
        _current_yaml().replace(b"26.0.1", b"999.0.0"),
        b"schema_name: tara.merged_transcription\n"
        b"schema_version: 26.0.1\nmetadata: &meta {}\n"
        b"content:\n  text: test\n  segments: []\n  model: *meta\n",
    ],
)
def test_hostile_or_unknown_yaml_is_rejected(
    tmp_path: Path, content: bytes
) -> None:
    row = _validate(tmp_path, content)
    assert row["status"] == "invalid"
    assert row["validation_error_code"] == "input_invalid"
    assert row["schema_name"] is None
    assert row["token_count"] is None


def test_token_overflow_is_rejected_without_truncation(tmp_path: Path) -> None:
    row = _validate(tmp_path, _current_yaml("beaucoup de texte"), max_tokens=1)
    assert row["status"] == "invalid"
    assert row["validation_error_code"] == "input_too_large"
    assert row["token_count"] is None
