from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tara.schemas.merged_transcription import new_merged_transcription
from tara.yaml_utils import to_yaml
from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import migrate
from tara_web.db.repositories.artifacts import ArtifactRepository
from tara_web.orchestration.job_service import JobService
from tara_web.orchestration.job_workspace import (
    JobWorkspaceError,
    JobWorkspaceService,
    parse_manifest,
)
from tara_web.orchestration.process_pool import ProcessPool
from tara_web.services.upload_sessions import new_opaque_id
from tara_web.storage.artifacts import ArtifactPolicy, ArtifactService
from tara_web.storage.atomic import write_staged
from tara_web.storage.cleanup import cleanup_expired, cleanup_orphans
from tara_web.storage.layout import StorageLayout
from tara_web.storage.reconciliation import reconcile


@dataclass(frozen=True)
class SeededJob:
    database: ConnectionFactory
    layout: StorageLayout
    root: Path
    sources: tuple[Path, ...]
    contents: tuple[bytes, ...]


def seed_audio_job(tmp_path: Path) -> SeededJob:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    database = ConnectionFactory(root / "tara.sqlite3", root)
    connection = database.connect()
    contents = (b"first audio", b"second audio")
    sources: list[Path] = []
    try:
        migrate(connection)
        now = "2026-01-01T00:00:00Z"
        connection.execute(
            "INSERT INTO upload_sessions("
            "public_id,secret_hmac,status,reserved_bytes,expires_at,created_at,"
            "updated_at) VALUES('session_000000000001',?,'consumed',0,?,?,?)",
            ("v1:" + "a" * 64, now, now, now),
        )
        connection.execute(
            "INSERT INTO jobs("
            "public_id,upload_session_id,secret_hmac,status,pipeline_version,"
            "job_type,language,expires_at,input_file_count,created_at,updated_at) "
            "VALUES('job_0000000000001',1,?,'queued','v1','audio','fr',?,2,?,?)",
            ("v1:" + "a" * 64, now, now, now),
        )
        connection.execute(
            "INSERT INTO job_attempts(job_id,attempt_number,status) "
            "VALUES(1,1,'queued')"
        )
        layout = StorageLayout(root)
        for index, content in enumerate(contents, start=1):
            file_id = f"file_00000000000{index}"
            managed = layout.upload_file("session_000000000001", file_id)
            source = layout.upload_path(managed.relative_path)
            source.write_bytes(content)
            sources.append(source)
            connection.execute(
                "INSERT INTO upload_files("
                "public_id,session_id,job_id,status,storage_path,declared_bytes,"
                "sha256_hex,active,person,display_name,declared_mime,detected_type,"
                "duration_ms,created_at,updated_at) "
                "VALUES(?,?,1,'ready',?,?,?,?,?,?,?,?,?,?,?)",
                (
                    file_id,
                    1,
                    managed.relative_path,
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                    1,
                    f"Person {index}",
                    f"track-{index}.mp3",
                    "audio/mpeg",
                    "mp3",
                    1_000 + index,
                    now,
                    now,
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return SeededJob(database, layout, root, tuple(sources), contents)


def seed_merged_job(tmp_path: Path) -> SeededJob:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    database = ConnectionFactory(root / "tara.sqlite3", root)
    connection = database.connect()
    content = to_yaml(
        new_merged_transcription(
            text="Une session deja transcrite.", segments=[]
        ).to_dict()
    ).encode()
    try:
        migrate(connection)
        now = "2026-01-01T00:00:00Z"
        connection.execute(
            "INSERT INTO upload_sessions("
            "public_id,secret_hmac,status,reserved_bytes,expires_at,created_at,"
            "updated_at,input_type,context_text,previous_summaries_text) "
            "VALUES('session_merged_000001',?,'consumed',0,?,?,?,"
            "'merged_transcription','contexte','resume precedent')",
            ("v1:" + "a" * 64, now, now, now),
        )
        connection.execute(
            "INSERT INTO jobs("
            "public_id,upload_session_id,secret_hmac,status,pipeline_version,"
            "job_type,language,expires_at,input_file_count,created_at,updated_at) "
            "VALUES('job_merged_00000001',1,?,'queued','v1',"
            "'merged_transcription','fr',?,1,?,?)",
            ("v1:" + "a" * 64, now, now, now),
        )
        connection.execute(
            "INSERT INTO job_attempts(job_id,attempt_number,status) "
            "VALUES(1,1,'queued')"
        )
        layout = StorageLayout(root)
        managed = layout.upload_file(
            "session_merged_000001", "file_merged_00000001"
        )
        source = layout.upload_path(managed.relative_path)
        source.write_bytes(content)
        connection.execute(
            "INSERT INTO upload_files("
            "public_id,session_id,job_id,status,storage_path,declared_bytes,"
            "sha256_hex,active,display_name,declared_mime,schema_name,"
            "schema_version,token_count,created_at,updated_at) "
            "VALUES('file_merged_00000001',1,1,'ready',?,?,?,?,"
            "'transcription.yaml','application/yaml','tara.merged_transcription',"
            "'26.0.1',6,?,?)",
            (
                managed.relative_path,
                len(content),
                hashlib.sha256(content).hexdigest(),
                1,
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return SeededJob(database, layout, root, (source,), (content,))


def journal_rows(job: SeededJob) -> list[object]:
    connection = job.database.connect()
    try:
        return connection.execute(
            "SELECT * FROM job_input_preparations ORDER BY upload_file_id"
        ).fetchall()
    finally:
        connection.close()


def artifact_service(job: SeededJob) -> ArtifactService:
    return ArtifactService(
        job.layout,
        ArtifactRepository(job.database),
        ArtifactPolicy(max_bytes=1_048_576, minimum_free_bytes=0),
    )


def set_session_texts(job: SeededJob, context: str, previous: str) -> None:
    with job.database.transaction() as connection:
        connection.execute(
            "UPDATE upload_sessions SET context_text=?,previous_summaries_text=? "
            "WHERE id=1",
            (context, previous),
        )


def text_artifact_rows(job: SeededJob) -> list[object]:
    connection = job.database.connect()
    try:
        return connection.execute(
            "SELECT * FROM job_artifacts WHERE artifact_type IN "
            "('context_input','previous_summary_input') ORDER BY id"
        ).fetchall()
    finally:
        connection.close()


def test_audio_claim_prepares_ordered_immutable_manifest(tmp_path: Path) -> None:
    job = seed_audio_job(tmp_path)
    workspace = JobWorkspaceService(job.database, job.layout)
    service = JobService(job.database, workspace_service=workspace)

    claim = service.claim_next()

    assert claim is not None
    request, _ = claim
    manifest_path = job.root / "jobs" / request.job_id / request.source_manifest_path
    manifest = parse_manifest(manifest_path.read_bytes(), request.job_id)
    assert [item["person"] for item in manifest["inputs"]] == [
        "Person 1",
        "Person 2",
    ]
    assert all(
        (job.root / "jobs" / request.job_id / item["path"]).is_file()
        for item in manifest["inputs"]
    )
    connection = job.database.connect()
    try:
        snapshot = connection.execute(
            "SELECT request_json FROM job_run_snapshots"
        ).fetchone()[0]
    finally:
        connection.close()
    assert str(job.root) not in snapshot
    assert "secret" not in snapshot.lower()


def test_merged_claim_prepares_integrity_checked_yaml_and_texts(tmp_path: Path) -> None:
    job = seed_merged_job(tmp_path)
    service = JobService(
        job.database,
        workspace_service=JobWorkspaceService(
            job.database, job.layout, artifact_service=artifact_service(job)
        ),
    )

    claim = service.claim_next()

    assert claim is not None
    request, _ = claim
    assert request.input_kind == "merged_transcription"
    assert request.source_manifest_path is None
    assert request.merged_transcription_path is not None
    assert request.merged_transcription_path.endswith(".yaml")
    assert request.context_path is not None
    assert request.previous_summaries_path is not None
    prepared = job.root / "jobs" / request.job_id / request.merged_transcription_path
    assert prepared.read_bytes() == job.contents[0]
    assert not job.sources[0].exists()


@pytest.mark.parametrize("hook_name", ["before_rename", "after_rename"])
def test_interrupted_rename_is_resumed(tmp_path: Path, hook_name: str) -> None:
    job = seed_audio_job(tmp_path)

    def interrupt() -> None:
        raise JobWorkspaceError("simulated interruption")

    kwargs = {hook_name: interrupt}
    interrupted = JobWorkspaceService(job.database, job.layout, **kwargs)
    with pytest.raises(JobWorkspaceError, match="simulated interruption"):
        interrupted.prepare(1, "job_0000000000001")

    recovered = JobWorkspaceService(job.database, job.layout)
    assert recovered.prepare(1, "job_0000000000001") == ("inputs/source-manifest.json")
    rows = journal_rows(job)
    assert len(rows) == 2
    assert all(row["state"] == "moved" for row in rows)
    for row, content in zip(rows, job.contents, strict=True):
        target = job.layout.artifact_path(row["destination_path"])
        assert target.read_bytes() == content
    assert not any(source.exists() for source in job.sources)


def test_prepare_is_idempotent_and_concurrent_claim_is_single(tmp_path: Path) -> None:
    job = seed_audio_job(tmp_path)
    workspace = JobWorkspaceService(job.database, job.layout)
    assert workspace.prepare(1, "job_0000000000001") == ("inputs/source-manifest.json")
    assert workspace.prepare(1, "job_0000000000001") == ("inputs/source-manifest.json")
    service = JobService(job.database, workspace_service=workspace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: service.claim_next(), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    connection = job.database.connect()
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM job_run_snapshots").fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_session_text_artifacts_are_idempotent_and_claim_safe(tmp_path: Path) -> None:
    job = seed_audio_job(tmp_path)
    set_session_texts(job, "Contexte de campagne.", "Resume precedent.")
    workspace = JobWorkspaceService(
        job.database, job.layout, artifact_service=artifact_service(job)
    )

    first = workspace.prepare_request_paths(1, "job_0000000000001")
    second = workspace.prepare_request_paths(1, "job_0000000000001")

    assert first == second
    assert first[1] is not None and first[1].endswith(".bin")
    assert first[2] is not None and first[2].endswith(".bin")
    rows = text_artifact_rows(job)
    assert [row["artifact_type"] for row in rows] == [
        "context_input",
        "previous_summary_input",
    ]
    assert all(row["storage_state"] == "ready" for row in rows)

    service = JobService(job.database, workspace_service=workspace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: service.claim_next(), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    assert len(text_artifact_rows(job)) == 2


def test_session_text_artifacts_expire_with_intermediate_retention(
    tmp_path: Path,
) -> None:
    job = seed_audio_job(tmp_path)
    set_session_texts(job, "Contexte", "Resume")
    workspace = JobWorkspaceService(
        job.database, job.layout, artifact_service=artifact_service(job)
    )
    paths = workspace.prepare_request_paths(1, "job_0000000000001")
    rows = text_artifact_rows(job)
    assert all(row["retention_kind"] == "intermediate" for row in rows)
    assert all(
        (job.root / "jobs" / "job_0000000000001" / str(path)).exists()
        for path in paths[1:]
        if path is not None
    )
    with job.database.transaction() as connection:
        connection.execute(
            "UPDATE job_artifacts SET expires_at='2020-01-01T00:00:00+00:00' "
            "WHERE artifact_type IN ('context_input','previous_summary_input')"
        )
    assert cleanup_expired(
        job.layout,
        ArtifactRepository(job.database),
        batch_size=10,
        now=datetime(2026, 1, 1, tzinfo=UTC),
        sleeper=lambda _: None,
    ) == 2
    assert all(
        not (job.root / "jobs" / "job_0000000000001" / str(path)).exists()
        for path in paths[1:]
        if path is not None
    )


def test_session_text_preparation_reuses_reconciled_staged_and_replaces_pending(
    tmp_path: Path,
) -> None:
    job = seed_audio_job(tmp_path)
    set_session_texts(job, "Contexte", "Resume precedent")
    repository = ArtifactRepository(job.database)
    pending = job.layout.new_artifact("job_0000000000001", "context_input")
    repository.create_pending(
        job_id=1,
        artifact_type="context_input",
        retention_kind="intermediate",
        relative_path=pending.relative_path,
        original_filename=None,
        max_per_job=64,
    )
    staged = job.layout.new_artifact("job_0000000000001", "previous_summary_input")
    staged_id = repository.create_pending(
        job_id=1,
        artifact_type="previous_summary_input",
        retention_kind="intermediate",
        relative_path=staged.relative_path,
        original_filename=None,
        max_per_job=64,
    )
    write_staged(
        job.layout,
        staged,
        (b"Resume precedent",),
        max_bytes=1_048_576,
        minimum_free_bytes=0,
        hash_content=True,
        stage=lambda value: repository.stage(
            staged_id,
            byte_size=value.byte_size,
            sha256_hex=value.sha256_hex,
            temporary_name=value.temporary_name,
        ),
    )
    assert reconcile(job.layout, repository, limit=10) == (2, 2)

    workspace = JobWorkspaceService(
        job.database, job.layout, artifact_service=artifact_service(job)
    )
    _, context_path, previous_path = workspace.prepare_request_paths(
        1, "job_0000000000001"
    )

    assert context_path is not None and previous_path == f"inputs/{staged.filename}"
    rows = text_artifact_rows(job)
    assert len([row for row in rows if row["artifact_type"] == "context_input"]) == 2
    assert len(
        [row for row in rows if row["artifact_type"] == "previous_summary_input"]
    ) == 1


@pytest.mark.parametrize("failure", ["missing", "hash", "ambiguous"])
def test_invalid_move_states_are_rejected(tmp_path: Path, failure: str) -> None:
    job = seed_audio_job(tmp_path)
    if failure == "missing":
        job.sources[0].unlink()
    elif failure == "hash":
        job.sources[0].write_bytes(b"wrong audio")
    else:

        def stop_before_rename() -> None:
            raise JobWorkspaceError("stop")

        interrupted = JobWorkspaceService(
            job.database,
            job.layout,
            before_rename=stop_before_rename,
        )
        with pytest.raises(JobWorkspaceError, match="stop"):
            interrupted.prepare(1, "job_0000000000001")
        row = journal_rows(job)[0]
        target = job.layout.artifact_path(row["destination_path"])
        target.write_bytes(job.contents[0])

    with pytest.raises(JobWorkspaceError):
        JobWorkspaceService(job.database, job.layout).prepare(1, "job_0000000000001")


def test_symlink_source_is_rejected(tmp_path: Path) -> None:
    job = seed_audio_job(tmp_path)
    source = job.sources[0]
    content = source.read_bytes()
    source.unlink()
    target = source.with_name("external.bin")
    target.write_bytes(content)
    try:
        source.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable on this Windows host")
    with pytest.raises(JobWorkspaceError):
        JobWorkspaceService(job.database, job.layout).prepare(1, "job_0000000000001")


def test_cleanup_preserves_workspace_until_job_is_deleted(tmp_path: Path) -> None:
    job = seed_audio_job(tmp_path)
    workspace = JobWorkspaceService(job.database, job.layout)
    workspace.prepare(1, "job_0000000000001")
    rows = journal_rows(job)
    input_dir = job.root / "jobs" / "job_0000000000001" / "inputs"
    unknown = input_dir / ("f" * 32 + ".bin")
    unknown.write_bytes(b"unknown")
    old = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    for path in input_dir.iterdir():
        os.utime(path, (old, old))

    cleanup_orphans(
        job.layout,
        ArtifactRepository(job.database),
        grace_seconds=1,
        batch_size=100,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    manifest = input_dir / "source-manifest.json"
    assert manifest.exists()
    assert all(
        job.layout.artifact_path(row["destination_path"]).exists() for row in rows
    )
    assert not unknown.exists()

    with job.database.transaction() as connection:
        connection.execute("UPDATE jobs SET status='deleted' WHERE id=1")
    cleanup_orphans(
        job.layout,
        ArtifactRepository(job.database),
        grace_seconds=1,
        batch_size=100,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert not manifest.exists()
    assert not any(
        job.layout.artifact_path(row["destination_path"]).exists() for row in rows
    )


def test_manifest_parser_rejects_traversal_and_wrong_scalar_types() -> None:
    base = {
        "version": 1,
        "job_id": "job_0000000000001",
        "inputs": [
            {
                "path": "inputs/" + "a" * 32 + ".bin",
                "person": "Alice",
                "display_name": "Alice.mp3",
                "mime": "audio/mpeg",
                "detected_type": "mp3",
                "duration_ms": 1,
                "bytes": 1,
                "sha256": "b" * 64,
                "source_id": "file_000000000001",
            }
        ],
    }
    for field, value in (("path", "inputs/../x.bin"), ("bytes", True)):
        payload = json.loads(json.dumps(base))
        payload["inputs"][0][field] = value
        with pytest.raises(JobWorkspaceError):
            parse_manifest(
                json.dumps(payload).encode(),
                "job_0000000000001",
            )


def test_workspace_manifest_parser_accepts_real_upload_file_id() -> None:
    payload = {
        "version": 1,
        "job_id": "job_0000000000001",
        "inputs": [
            {
                "path": "inputs/" + "a" * 32 + ".bin",
                "person": "Alice",
                "display_name": "alice.mp3",
                "mime": "audio/mpeg",
                "detected_type": "mp3",
                "duration_ms": 1,
                "bytes": 1,
                "sha256": "b" * 64,
                "source_id": new_opaque_id("uf"),
            }
        ],
    }
    assert parse_manifest(
        json.dumps(payload).encode(), "job_0000000000001"
    ) == payload


def test_spawn_success_promotes_one_public_yaml(tmp_path: Path) -> None:
    job = seed_audio_job(tmp_path)
    repository = ArtifactRepository(job.database)
    artifacts = ArtifactService(
        job.layout,
        repository,
        ArtifactPolicy(
            max_bytes=1_048_576,
            minimum_free_bytes=0,
            orphan_grace_seconds=1,
        ),
    )
    service = JobService(
        job.database,
        artifact_service=artifacts,
        workspace_service=JobWorkspaceService(job.database, job.layout),
    )
    claim = service.claim_next()
    assert claim is not None
    request, token = claim
    pool = ProcessPool(1, 16)
    pool.submit(request, token, "success", service.workspace_for(request.job_id))
    completed = []
    for _ in range(500):
        for payload in pool.take_events():
            service.apply_ipc(payload)
        completed = pool.finished(
            timeout_seconds=20,
            cancellation_grace_seconds=1,
        )
        if completed:
            break
        time.sleep(0.02)
    pool.close()
    assert len(completed) == 1
    _, result, reason = completed[0]
    assert reason is None and result is not None
    assert service.finish(request.job_id, request.attempt_number, token, result)

    connection = job.database.connect()
    try:
        status = connection.execute("SELECT status FROM jobs WHERE id=1").fetchone()[0]
        artifact = connection.execute(
            "SELECT id FROM job_artifacts WHERE job_id=1 "
            "AND artifact_type='final_yaml' AND storage_state='ready'"
        ).fetchone()
    finally:
        connection.close()
    assert status == "completed"
    assert artifact is not None
    final_yaml = artifacts.read_final_yaml(artifact_id=artifact[0], job_id=1)
    assert b"schema_name: tara.public_result" in final_yaml
    assert b"schema_version: 26.0.1" in final_yaml
    assert not (service.workspace_for(request.job_id) / "work" / "final.yaml").exists()
