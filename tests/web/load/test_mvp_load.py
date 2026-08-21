"""Provider-free acceptance load for admission, polling and realtime limits."""

from __future__ import annotations

import asyncio
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import pytest

from tara.token_limits import require_token_limit
from tara_web.db.connection import ConnectionFactory, DatabaseConflict
from tara_web.db.migrations import migrate
from tara_web.db.repositories.jobs import JobRepository
from tara_web.domain.models import Job
from tara_web.orchestration.job_service import JobService
from tara_web.realtime.broker import EventBroker, RealtimeLimitExceeded


class _MergedWorkspace:
    def prepare_merged_request_paths(
        self, _job_id: int, _public_id: str
    ) -> tuple[str, None, None]:
        return ("inputs/" + "a" * 32 + ".yaml", None, None)

    def recover(self) -> list[int]:
        return []


def _seed_ready_sessions(factory: ConnectionFactory, count: int) -> None:
    now = datetime.now(UTC).isoformat()
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    secret = "v1:" + "a" * 64
    with factory.transaction() as connection:
        for index in range(count):
            session_id = f"session_{index:016d}"
            connection.execute(
                "INSERT INTO upload_sessions(public_id,secret_hmac,status,revision,"
                "reserved_bytes,expires_at,created_at,updated_at,input_type) "
                "VALUES(?,?,'ready',1,1,?,?,?,'merged_transcription')",
                (session_id, secret, expires, now, now),
            )
            connection.execute(
                "INSERT INTO upload_files(public_id,session_id,status,storage_path,"
                "declared_bytes,confirmed_offset,sha256_hex,active,created_at,"
                "updated_at) SELECT ?,id,'ready',?,1,1,?,1,?,? FROM upload_sessions "
                "WHERE public_id=?",
                (
                    f"file_{index:016d}",
                    f"uploads/{session_id}/file_{index:016d}.part",
                    "0" * 64,
                    now,
                    now,
                    session_id,
                ),
            )


def test_five_active_twenty_five_waiting_remain_bounded(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    factory = ConnectionFactory(root / "tara.sqlite3", root, busy_timeout_ms=10_000)
    connection = factory.connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    _seed_ready_sessions(factory, 31)

    # Keep the timed admission window independent from test order. The first token
    # check initializes tiktoken and is deliberately part of warm-up, not load.
    require_token_limit("", 2_000)

    repository = JobRepository(factory)
    secret = "v1:" + "a" * 64
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    admission_latencies: list[float] = []
    for index in range(30):
        started = perf_counter()
        repository.promote(
            Job(
                f"job_{index:016d}",
                f"session_{index:016d}",
                secret,
                "load-test",
                expires,
                job_type="merged_transcription",
            ),
            expected_session_revision=1,
            max_waiting_jobs=30,
        )
        admission_latencies.append(perf_counter() - started)

    service = JobService(
        factory, max_waiting_jobs=25, workspace_service=_MergedWorkspace()
    )
    claims = [service.claim_next(max_active_jobs=5) for _ in range(5)]
    assert all(claim is not None for claim in claims)
    assert service.claim_next(max_active_jobs=5) is None

    with pytest.raises(DatabaseConflict, match="capacity"):
        repository.promote(
            Job(
                "job_9999999999999999",
                "session_0000000000000030",
                secret,
                "load-test",
                expires,
                job_type="merged_transcription",
            ),
            expected_session_revision=1,
            max_waiting_jobs=25,
        )

    def poll() -> tuple[int, int]:
        started = perf_counter()
        database = factory.connect()
        try:
            counts = database.execute(
                "SELECT SUM(status IN ('running','cancel_requested','stopping')),"
                "SUM(status='queued') FROM jobs"
            ).fetchone()
            assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            return int((perf_counter() - started) * 1_000_000), int(counts[1])
        finally:
            database.close()

    with ThreadPoolExecutor(max_workers=12) as executor:
        polling = list(executor.map(lambda _: poll(), range(60)))
    assert all(waiting == 25 for _, waiting in polling)
    assert statistics.quantiles([latency for latency, _ in polling], n=20)[18] < 250_000

    broker = EventBroker(maximum=12, per_job=4)
    subscriptions = []
    for index in range(5):
        for _ in range(2):
            job_id = f"job_{index:016d}"
            subscriptions.append((job_id, broker.subscribe(job_id)))
    with pytest.raises(RealtimeLimitExceeded):
        for _ in range(3):
            broker.subscribe("job_overflow")
    for index in range(5):
        broker.publish(
            f"job_{index:016d}",
            {"type": "snapshot_updated", "revision": 2, "data": {}},
        )
    assert all(asyncio.run(queue.get())["revision"] == 2 for _, queue in subscriptions)
    for job_id, queue in subscriptions:
        broker.unsubscribe(job_id, queue)

    assert service.request_cancel("job_0000000000000000")
    assert service.request_cancel("job_0000000000000005")
    queued_after_restart = service.recover()
    assert len(queued_after_restart) == 24

    database = factory.connect()
    try:
        rows = database.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status")
        counts = dict(rows)
        assert counts == {"cancelled": 1, "failed": 5, "queued": 24}
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        database.close()
    assert max(admission_latencies) < 1.0
