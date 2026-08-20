from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import tara_web.app as application
from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import MIGRATIONS, migrate, schema_version
from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.services.chunk_upload import ChunkUploadService
from tara_web.services.idempotency import SecretHmac
from tara_web.services.upload_maintenance import drain_startup_uploads, maintain_uploads
from tara_web.services.upload_sessions import UploadSessionService, UploadUnauthorized
from tara_web.storage.layout import StorageLayout


def upload_stack(
    tmp_path: Path,
    *,
    max_files: int = 20,
    max_sessions: int = 20,
    max_reserved_bytes: int = 10_000,
) -> tuple[
    ConnectionFactory,
    AudioUploadRepository,
    StorageLayout,
    UploadSessionService,
    ChunkUploadService,
]:
    root = tmp_path / "runtime"
    root.mkdir()
    factory = ConnectionFactory(root / "tara.sqlite3", root)
    connection = factory.connect()
    try:
        assert migrate(connection) == 16
    finally:
        connection.close()
    repository = AudioUploadRepository(factory)
    layout = StorageLayout(root)
    sessions = UploadSessionService(
        repository,
        layout,
        SecretHmac(b"m" * 32),
        max_files=max_files,
        max_sessions=max_sessions,
        max_reserved_bytes=max_reserved_bytes,
        max_upload_bytes=max_reserved_bytes,
    )
    chunks = ChunkUploadService(
        sessions, repository, layout, max_chunk_bytes=max_reserved_bytes
    )
    return factory, repository, layout, sessions, chunks


def declare(
    sessions: UploadSessionService,
    session_id: str,
    secret: str,
    name: str,
    content: bytes,
) -> dict[str, object]:
    return sessions.declare_file(
        session_id,
        secret,
        filename=name,
        size=len(content),
        sha256_hex=hashlib.sha256(content).hexdigest(),
        mime="audio/mpeg",
    )


def test_maintenance_truncates_suffix_and_marks_tombstone_clean(tmp_path: Path) -> None:
    factory, repository, layout, sessions, _ = upload_stack(tmp_path)
    content = b"ID3x"
    session = sessions.create()
    declared = sessions.declare_file(
        session.session_id,
        session.secret,
        filename="Alice.mp3",
        size=len(content),
        sha256_hex=hashlib.sha256(content).hexdigest(),
        mime="audio/mpeg",
    )
    row = repository.file_for_session(session.session_id, str(declared["file_id"]))
    path = layout.upload_path(str(row["storage_path"]))
    path.write_bytes(content + b"suffix")
    drain_startup_uploads(repository, layout, batch_size=10)
    assert path.stat().st_size == 0
    assert repository.delete_file(session.session_id, str(declared["file_id"]), 1) == 2
    maintain_uploads(repository, layout, batch_size=10)
    assert not path.exists()
    connection = factory.connect()
    try:
        assert connection.execute(
            "SELECT storage_cleaned_at FROM upload_files WHERE public_id=?",
            (declared["file_id"],),
        ).fetchone()[0]
    finally:
        connection.close()


def test_startup_drain_visits_more_than_one_batch_without_creating_empty_files(
    tmp_path: Path,
) -> None:
    _, repository, layout, sessions, chunks = upload_stack(tmp_path)
    session = sessions.create()
    empty_paths: list[Path] = []
    short_ids: list[str] = []
    for index in range(7):
        content = f"ID3-{index}".encode()
        item = declare(
            sessions, session.session_id, session.secret, f"Player{index}.mp3", content
        )
        row = repository.file_for_session(session.session_id, str(item["file_id"]))
        assert row is not None
        path = layout.upload_path(str(row["storage_path"]))
        if index % 3 == 0:
            empty_paths.append(path)
        elif index % 3 == 1:
            path.write_bytes(content + b"suffix")
        else:
            chunks.write(
                session.session_id,
                str(item["file_id"]),
                session.secret,
                offset=0,
                digest=hashlib.sha256(content).hexdigest(),
                content=content,
            )
            path.write_bytes(content[:-1])
            short_ids.append(str(item["file_id"]))

    assert drain_startup_uploads(repository, layout, batch_size=2) == 7
    assert all(not path.exists() for path in empty_paths)
    for file_id in short_ids:
        row = repository.file_for_session(session.session_id, file_id)
        assert row is not None and row["status"] == "invalid"


def test_tombstones_are_globally_bounded_and_not_reselected(tmp_path: Path) -> None:
    factory, repository, layout, sessions, _ = upload_stack(tmp_path)
    session = sessions.create()
    for index in range(5):
        item = declare(
            sessions,
            session.session_id,
            session.secret,
            f"Deleted{index}.mp3",
            b"ID3x",
        )
        repository.delete_file(session.session_id, str(item["file_id"]), 1)

    assert maintain_uploads(repository, layout, batch_size=2) == 2
    assert maintain_uploads(repository, layout, batch_size=2) == 2
    assert maintain_uploads(repository, layout, batch_size=2) == 1
    assert maintain_uploads(repository, layout, batch_size=2) == 0
    connection = factory.connect()
    try:
        cleaned = connection.execute(
            "SELECT COUNT(*) FROM upload_files WHERE storage_cleaned_at IS NOT NULL"
        ).fetchone()[0]
        assert cleaned == 5
    finally:
        connection.close()


def test_failed_unlink_is_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, repository, layout, sessions, _ = upload_stack(tmp_path)
    session = sessions.create()
    item = declare(sessions, session.session_id, session.secret, "Retry.mp3", b"ID3x")
    repository.delete_file(session.session_id, str(item["file_id"]), 1)
    calls = 0

    import tara_web.services.upload_maintenance as maintenance

    original = maintenance.unlink_upload

    def flaky(*args: object, **kwargs: object) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected")
        return original(*args, **kwargs)

    monkeypatch.setattr(maintenance, "unlink_upload", flaky)
    assert maintain_uploads(repository, layout, batch_size=1) == 1
    assert maintain_uploads(repository, layout, batch_size=1) == 1
    assert maintain_uploads(repository, layout, batch_size=1) == 0


def test_expired_session_is_immediately_unreadable_and_excluded_from_quotas(
    tmp_path: Path,
) -> None:
    factory, _, _, sessions, _ = upload_stack(
        tmp_path, max_sessions=1, max_reserved_bytes=4
    )
    expired = sessions.create()
    declare(sessions, expired.session_id, expired.secret, "Old.mp3", b"ID3x")
    connection = factory.connect()
    try:
        connection.execute(
            "UPDATE upload_sessions SET expires_at=? WHERE public_id=?",
            (
                (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                expired.session_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(UploadUnauthorized):
        sessions.authorize(expired.session_id, expired.secret)
    current = sessions.create()
    declare(sessions, current.session_id, current.secret, "New.mp3", b"ID3x")


def test_periodic_maintenance_survives_one_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, repository, layout, _, _ = upload_stack(tmp_path)
    calls = 0

    def flaky(*args: object, **kwargs: object) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected")
        return 0

    monkeypatch.setattr(application, "maintain_uploads", flaky)

    async def scenario() -> None:
        task = asyncio.create_task(
            application._upload_maintenance(repository, layout, 1, 0)  # noqa: SLF001
        )
        try:
            for _ in range(100):
                if calls >= 2:
                    break
                await asyncio.sleep(0.001)
            assert calls >= 2
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_upgrade_from_populated_v5_preserves_upload_data(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    factory = ConnectionFactory(root / "tara.sqlite3", root)
    connection = factory.connect()
    try:
        assert migrate(connection, registry=MIGRATIONS[:5]) == 5
        now = datetime.now(UTC).isoformat()
        session_id = connection.execute(
            "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
            "created_at,updated_at) VALUES (?,?, 'uploading',?,?,?) RETURNING id",
            ("session-upgrade-0001", "v1:" + "a" * 64, now, now, now),
        ).fetchone()[0]
        file_id = connection.execute(
            "INSERT INTO upload_files(public_id,session_id,status,storage_path,"
            "original_filename,declared_bytes,confirmed_offset,sha256_hex,"
            "created_at,updated_at) VALUES "
            "(?,?, 'uploading',?,?,4,4,?,?,?) RETURNING id",
            (
                "file-upgrade-000001",
                session_id,
                "uploads/session-upgrade-0001/file-upgrade-000001.part",
                "Alice.mp3",
                "b" * 64,
                now,
                now,
            ),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO upload_chunks(file_id,offset_bytes,byte_count,sha256_hex,"
            "created_at) VALUES (?,0,4,?,?)",
            (file_id, "c" * 64, now),
        )
        connection.execute(
            "INSERT INTO idempotency_keys(operation,owner_hmac,key_hmac,"
            "request_fingerprint,created_at,expires_at) VALUES (?,?,?,?,?,?)",
            ("upgrade", "v1:" + "d" * 64, "v1:" + "e" * 64, "f" * 64, now, now),
        )
        connection.commit()

        assert migrate(connection) == 16
        assert schema_version(connection) == 16
        upgraded = connection.execute(
            "SELECT person,chunk_size,storage_cleaned_at FROM upload_files WHERE id=?",
            (file_id,),
        ).fetchone()
        assert upgraded is not None
        assert upgraded[0] is None and upgraded[1] == 1_048_576 and upgraded[2] is None
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM upload_chunks WHERE file_id=?", (file_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM idempotency_keys WHERE operation='upgrade'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        indices = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_upload_files_cleanup" in indices
        assert "idx_upload_validations_queue" in indices
    finally:
        connection.close()


def test_generated_upload_hmac_key_is_stable_and_strict(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    config = type("Config", (), {"upload_hmac_key": None})()
    first = application._load_upload_hmac_key(root, config)  # noqa: SLF001
    second = application._load_upload_hmac_key(root, config)  # noqa: SLF001
    assert first == second and len(first) == 32
    key_path = root / ".upload-hmac-key"
    assert key_path.read_bytes() == first
    if application.os.name != "nt":
        assert key_path.stat().st_mode & 0o077 == 0

    key_path.write_bytes(b"short")
    with pytest.raises(RuntimeError, match="HMAC key is invalid"):
        application._load_upload_hmac_key(root, config)  # noqa: SLF001
