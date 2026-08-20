from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate, schema_version
from tara_web.db.repositories.audio_uploads import AudioUploadRepository
from tara_web.services.chunk_upload import ChunkUploadService
from tara_web.services.idempotency import IdempotencyService, SecretHmac
from tara_web.services.upload_sessions import (
    CreatedSession,
    UploadSessionService,
    UploadUnauthorized,
    sanitize_display_name,
)
from tara_web.storage.layout import StorageError, StorageLayout

UploadStack = tuple[
    ConnectionFactory,
    AudioUploadRepository,
    StorageLayout,
    UploadSessionService,
    ChunkUploadService,
]


def stack(tmp_path: Path, *, max_files: int = 10, max_chunk: int = 1024) -> UploadStack:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
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
        SecretHmac(b"u" * 32),
        max_files=max_files,
        chunk_size=16_384,
    )
    return (
        factory,
        repository,
        layout,
        sessions,
        ChunkUploadService(sessions, repository, layout, max_chunk_bytes=max_chunk),
    )


def declared(
    sessions: UploadSessionService, content: bytes
) -> tuple[CreatedSession, dict[str, object]]:
    session = sessions.create()
    file = sessions.declare_file(
        session.session_id,
        session.secret,
        filename="Alice.mp3",
        size=len(content),
        sha256_hex=hashlib.sha256(content).hexdigest(),
        mime="audio/mpeg",
    )
    return session, file


def test_zip_session_accepts_one_resumable_archive_only(tmp_path: Path) -> None:
    _, repository, _, sessions, _ = stack(tmp_path)
    created = sessions.create(input_type="zip")
    content = b"PK archive"
    declared_zip = sessions.declare_file(
        created.session_id,
        created.secret,
        filename="tracks.ZIP",
        size=len(content),
        sha256_hex=hashlib.sha256(content).hexdigest(),
        mime="application/zip",
    )
    assert declared_zip["person"] is None
    row = repository.session(created.session_id)
    assert row is not None and row["archive_mode"] == 1
    with pytest.raises((ValueError, DatabaseConflict)):
        sessions.declare_file(
            created.session_id,
            created.secret,
            filename="other.zip",
            size=len(content),
            sha256_hex=hashlib.sha256(content).hexdigest(),
            mime="application/zip",
        )
    with pytest.raises(ValueError, match="type"):
        sessions.declare_file(
            created.session_id,
            created.secret,
            filename="audio.mp3",
            size=len(content),
            sha256_hex=hashlib.sha256(content).hexdigest(),
            mime="audio/mpeg",
        )


def test_secret_absent_invalid_and_unknown_are_uniform(tmp_path: Path) -> None:
    _, _, _, sessions, _ = stack(tmp_path)
    created = sessions.create()
    for session_id, secret in (
        (created.session_id, None),
        (created.session_id, "invalid"),
        ("unknown-session-0000", created.secret),
    ):
        with pytest.raises(UploadUnauthorized, match="resource unavailable"):
            sessions.authorize(session_id, secret)


def test_idempotent_session_creation_reuses_the_derived_secret(tmp_path: Path) -> None:
    factory, repository, layout, _, _ = stack(tmp_path)
    hmac_service = SecretHmac(b"i" * 32)
    sessions = UploadSessionService(
        repository,
        layout,
        hmac_service,
        idempotency=IdempotencyService(factory, hmac_service),
    )
    first = sessions.create("replay-key")
    replay = sessions.create("replay-key")
    assert replay == first
    assert int(repository.session(first.session_id)["revision"]) == 1
    sessions.authorize(first.session_id, first.secret)


def test_transactional_idempotency_rolls_back_reservation_and_effect(
    tmp_path: Path,
) -> None:
    factory, _, _, _, _ = stack(tmp_path)
    service = IdempotencyService(factory, SecretHmac(b"t" * 32))

    def broken() -> dict[str, object]:
        with factory.transaction() as connection:
            connection.execute(
                "INSERT INTO upload_sessions(public_id,secret_hmac,status,expires_at,"
                "created_at,updated_at) VALUES (?,?, 'created', ?, ?, ?)",
                (
                    "us_fault_0000000000",
                    "v1:" + "a" * 64,
                    "2100-01-01T00:00:00+00:00",
                    "x",
                    "x",
                ),
            )
        raise RuntimeError("fault")

    with pytest.raises(RuntimeError, match="fault"):
        service.transactional_execute("fault", "owner", "key", {"x": 1}, broken)
    connection = factory.connect()
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM upload_sessions").fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_transactional_session_creation_is_single_effect_under_concurrency(
    tmp_path: Path,
) -> None:
    factory, repository, layout, _, _ = stack(tmp_path)
    hmac_service = SecretHmac(b"c" * 32)
    sessions = UploadSessionService(
        repository,
        layout,
        hmac_service,
        idempotency=IdempotencyService(factory, hmac_service),
    )
    created: list[CreatedSession] = []

    def create() -> None:
        created.append(sessions.create("same-key"))

    workers = [threading.Thread(target=create) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len({item.session_id for item in created}) == 1
    assert len({item.secret for item in created}) == 1
    connection = factory.connect()
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM upload_sessions").fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_expired_session_rejects_chunk_mutation(tmp_path: Path) -> None:
    factory, _, _, sessions, chunks = stack(tmp_path)
    session, file = declared(sessions, b"ID3x")
    with factory.transaction() as connection:
        connection.execute(
            "UPDATE upload_sessions SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE public_id=?",
            (session.session_id,),
        )
    with pytest.raises(UploadUnauthorized):
        chunks.write(
            session.session_id,
            str(file["file_id"]),
            session.secret,
            offset=0,
            digest=hashlib.sha256(b"ID3x").hexdigest(),
            content=b"ID3x",
        )


def _write_declared(
    chunks: ChunkUploadService,
    session: CreatedSession,
    file: dict[str, object],
    content: bytes,
) -> None:
    assert chunks.write(
        session.session_id,
        str(file["file_id"]),
        session.secret,
        offset=0,
        digest=hashlib.sha256(content).hexdigest(),
        content=content,
    ) == len(content)


def test_chunk_storage_preserves_binary_newlines(tmp_path: Path) -> None:
    _, repository, layout, sessions, chunks = stack(tmp_path)
    content = b"ID3\x00\n\xff\ntrailer"
    session, file = declared(sessions, content)

    _write_declared(chunks, session, file, content)

    row = repository.file_for_session(session.session_id, str(file["file_id"]))
    assert row is not None
    assert layout.upload_path(str(row["storage_path"])).read_bytes() == content


def test_validating_one_file_does_not_block_other_files(tmp_path: Path) -> None:
    _, repository, _, sessions, chunks = stack(tmp_path)
    first_content, second_content = b"ID3first", b"ID3second"
    session, first = declared(sessions, first_content)
    second = sessions.declare_file(
        session.session_id,
        session.secret,
        filename="Bob.ogg",
        size=len(second_content),
        sha256_hex=hashlib.sha256(second_content).hexdigest(),
        mime="audio/ogg",
    )
    _write_declared(chunks, session, first, first_content)
    repository.queue_validation(
        session.session_id, str(first["file_id"]), 2, "uv_first_000000000"
    )
    assert repository.session(session.session_id)["status"] == "validating"
    _write_declared(chunks, session, second, second_content)
    repository.queue_validation(
        session.session_id, str(second["file_id"]), 2, "uv_second000000000"
    )
    assert repository.claim_validation("uv_first_000000000")
    repository.finish_validation(
        "uv_first_000000000",
        detected_type="mp3",
        duration_ms=1,
        warning_code=None,
        error_code=None,
    )
    assert repository.claim_validation("uv_second000000000")
    repository.finish_validation(
        "uv_second000000000",
        detected_type="ogg",
        duration_ms=1,
        warning_code=None,
        error_code=None,
    )
    assert repository.session(session.session_id)["status"] == "ready"


def test_replacement_activates_new_file_only_after_validation(tmp_path: Path) -> None:
    _, repository, _, sessions, chunks = stack(tmp_path)
    old_content, new_content = b"ID3old", b"ID3new"
    session, old = declared(sessions, old_content)
    _write_declared(chunks, session, old, old_content)
    repository.queue_validation(
        session.session_id, str(old["file_id"]), 2, "uv_old000000000000"
    )
    assert repository.claim_validation("uv_old000000000000")
    repository.finish_validation(
        "uv_old000000000000",
        detected_type="mp3",
        duration_ms=1,
        warning_code=None,
        error_code=None,
    )
    new = sessions.declare_file(
        session.session_id,
        session.secret,
        filename="Alice-new.mp3",
        size=len(new_content),
        sha256_hex=hashlib.sha256(new_content).hexdigest(),
        mime="audio/mpeg",
        replacement_for=str(old["file_id"]),
    )
    _write_declared(chunks, session, new, new_content)
    repository.queue_validation(
        session.session_id, str(new["file_id"]), 2, "uv_new000000000000"
    )
    assert repository.claim_validation("uv_new000000000000")
    repository.finish_validation(
        "uv_new000000000000",
        detected_type="mp3",
        duration_ms=1,
        warning_code=None,
        error_code=None,
    )
    old_row = repository.file_for_session(session.session_id, str(old["file_id"]))
    new_row = repository.file_for_session(session.session_id, str(new["file_id"]))
    assert old_row["status"] == "replaced" and old_row["active"] == 0
    assert new_row["status"] == "ready" and new_row["active"] == 1


def test_delete_cancels_queued_validation(tmp_path: Path) -> None:
    _, repository, _, sessions, chunks = stack(tmp_path)
    content = b"ID3delete"
    session, file = declared(sessions, content)
    _write_declared(chunks, session, file, content)
    repository.queue_validation(
        session.session_id, str(file["file_id"]), 2, "uv_delete000000000"
    )
    assert (
        sessions.delete_file(
            session.session_id, str(file["file_id"]), session.secret, 3
        )
        == 4
    )
    assert not repository.claim_validation("uv_delete000000000")


def test_chunk_replay_corruption_and_offset_do_not_advance(tmp_path: Path) -> None:
    _, repository, _, sessions, chunks = stack(tmp_path)
    content = b"ID3payload"
    session, file = declared(sessions, content)
    digest = hashlib.sha256(content).hexdigest()
    with pytest.raises(ValueError):
        chunks.write(
            session.session_id,
            file["file_id"],
            session.secret,
            offset=0,
            digest="0" * 64,
            content=content,
        )
    assert chunks.offset(session.session_id, file["file_id"], session.secret) == 0
    assert chunks.write(
        session.session_id,
        file["file_id"],
        session.secret,
        offset=0,
        digest=digest,
        content=content,
    ) == len(content)
    assert chunks.write(
        session.session_id,
        file["file_id"],
        session.secret,
        offset=0,
        digest=digest,
        content=content,
    ) == len(content)
    assert repository.file_for_session(session.session_id, file["file_id"])[
        "confirmed_offset"
    ] == len(content)


def test_restart_truncates_unconfirmed_suffix(tmp_path: Path) -> None:
    factory, repository, layout, sessions, chunks = stack(tmp_path)
    content = b"ID3payload"
    session, file = declared(sessions, content)
    first = content[:4]
    chunks.write(
        session.session_id,
        file["file_id"],
        session.secret,
        offset=0,
        digest=hashlib.sha256(first).hexdigest(),
        content=first,
    )
    row = repository.file_for_session(session.session_id, file["file_id"])
    path = layout.upload_path(str(row["storage_path"]))
    with path.open("ab") as handle:
        handle.write(b"unconfirmed")
    restarted = ChunkUploadService(
        sessions, AudioUploadRepository(factory), layout, max_chunk_bytes=1024
    )
    assert restarted.offset(session.session_id, file["file_id"], session.secret) == len(
        first
    )
    assert path.stat().st_size == len(first)


def test_two_clients_same_offset_receive_the_same_durable_result(
    tmp_path: Path,
) -> None:
    _, _, _, sessions, chunks = stack(tmp_path)
    content = b"ID3payload"
    session, file = declared(sessions, content)
    outcomes: list[object] = []

    def write() -> None:
        try:
            outcomes.append(
                chunks.write(
                    session.session_id,
                    file["file_id"],
                    session.secret,
                    offset=0,
                    digest=hashlib.sha256(content).hexdigest(),
                    content=content,
                )
            )
        except Exception as exc:
            outcomes.append(exc)

    workers = [threading.Thread(target=write) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert outcomes == [len(content), len(content)]


def test_names_and_file_limits_are_enforced(tmp_path: Path) -> None:
    _, _, _, sessions, _ = stack(tmp_path, max_files=1)
    session, _ = declared(sessions, b"ID3one")
    assert sanitize_display_name("Alice.mp3") == "Alice.mp3"
    with pytest.raises(ValueError):
        sanitize_display_name("bad\u202ename.mp3")
    with pytest.raises(DatabaseConflict):
        sessions.declare_file(
            session.session_id,
            session.secret,
            filename="Bob.ogg",
            size=4,
            sha256_hex="a" * 64,
            mime="audio/ogg",
        )


def test_migration_0006_adds_upload_validation_schema(tmp_path: Path) -> None:
    factory, _, _, _, _ = stack(tmp_path)
    connection = factory.connect()
    try:
        assert schema_version(connection) == 16
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='upload_validations'"
        ).fetchone()
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(upload_files)")
        }
        assert {
            "person",
            "chunk_size",
            "validation_attempt",
            "replacement_for_id",
        } <= columns
    finally:
        connection.close()


def test_database_failure_after_fsync_is_truncated_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, repository, layout, sessions, chunks = stack(tmp_path)
    content = b"ID3-crash-window"
    session, file = declared(sessions, content)

    def fail_advance(*_args: object, **_kwargs: object) -> int:
        raise DatabaseConflict("injected failure")

    monkeypatch.setattr(repository, "advance", fail_advance)
    with pytest.raises(DatabaseConflict):
        chunks.write(
            session.session_id,
            str(file["file_id"]),
            session.secret,
            offset=0,
            digest=hashlib.sha256(content).hexdigest(),
            content=content,
        )
    row = repository.file_for_session(session.session_id, str(file["file_id"]))
    assert row is not None
    path = layout.upload_path(str(row["storage_path"]))
    assert path.stat().st_size == len(content)
    monkeypatch.undo()
    assert chunks.offset(session.session_id, str(file["file_id"]), session.secret) == 0
    assert path.stat().st_size == 0


def test_upload_capacity_session_and_file_quotas_are_atomic(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    factory = ConnectionFactory(root / "tara.sqlite3", root)
    connection = factory.connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    sessions = UploadSessionService(
        AudioUploadRepository(factory),
        StorageLayout(root),
        SecretHmac(b"q" * 32),
        max_sessions=1,
        max_files=2,
        max_reserved_bytes=8,
        max_upload_bytes=8,
    )
    created = sessions.create()
    with pytest.raises(DatabaseConflict, match="session limit"):
        sessions.create()
    sessions.declare_file(
        created.session_id,
        created.secret,
        filename="one.mp3",
        size=6,
        sha256_hex="a" * 64,
        mime="audio/mpeg",
    )
    with pytest.raises(DatabaseConflict, match="capacity"):
        sessions.declare_file(
            created.session_id,
            created.secret,
            filename="two.ogg",
            size=3,
            sha256_hex="b" * 64,
            mime="audio/ogg",
        )


def test_chunk_checks_minimum_free_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, _, sessions, chunks = stack(tmp_path)
    chunks.minimum_free_bytes = 10
    session, file = declared(sessions, b"ID3x")
    monkeypatch.setattr(
        "tara_web.storage.uploads.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=4),
    )
    with pytest.raises(StorageError, match="capacity"):
        chunks.write(
            session.session_id,
            str(file["file_id"]),
            session.secret,
            offset=0,
            digest=hashlib.sha256(b"ID3x").hexdigest(),
            content=b"ID3x",
        )


@pytest.mark.parametrize(
    "relative",
    (
        "../outside.part",
        "uploads/session-00000000/nested/file-000000000.part",
        "uploads/session-00000000/file-000000000.mp3.part",
        "C:/uploads/session-00000000/file-000000000.part",
        r"uploads\session-00000000\file-000000000.part",
    ),
)
def test_upload_layout_rejects_unmanaged_paths(tmp_path: Path, relative: str) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    with pytest.raises(StorageError, match="path is invalid"):
        StorageLayout(root).parse_upload_path(relative)


def test_delete_and_cancel_release_files(tmp_path: Path) -> None:
    _, repository, layout, sessions, chunks = stack(tmp_path)
    content = b"ID3remove"
    session, file = declared(sessions, content)
    file_id = str(file["file_id"])
    chunks.write(
        session.session_id,
        file_id,
        session.secret,
        offset=0,
        digest=hashlib.sha256(content).hexdigest(),
        content=content,
    )
    row = repository.file_for_session(session.session_id, file_id)
    assert row is not None
    path = layout.upload_path(str(row["storage_path"]))
    assert sessions.delete_file(session.session_id, file_id, session.secret, 2) == 3
    assert not path.exists()
    current = sessions.authorize(session.session_id, session.secret)
    assert (
        sessions.cancel(session.session_id, session.secret, int(current["revision"]))
        == int(current["revision"]) + 1
    )
