from __future__ import annotations

from pathlib import Path

import pytest

from tara.providers.events import UsageAttempt
from tara.web_contracts import CONTRACT_VERSION, EventType, RunnerEvent, StageCode
from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import migrate
from tara_web.orchestration.ipc import IpcMessage
from tara_web.orchestration.job_service import JobService
from tara_web.services.circuit_breaker import CircuitBreaker

_NOW = "2026-07-18T10:00:00Z"
_TOKEN = "worker_token_00000000000000000001"


def _service(tmp_path: Path, jobs: int = 1) -> tuple[JobService, ConnectionFactory]:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    database = ConnectionFactory(root / "tara.sqlite3", root)
    connection = database.connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    with database.transaction() as connection:
        for number in range(1, jobs + 1):
            connection.execute(
                "INSERT INTO upload_sessions(public_id,secret_hmac,status,"
                "reserved_bytes,expires_at,created_at,updated_at) "
                "VALUES(?,?,'consumed',0,?,?,?)",
                (
                    f"session_00000000000{number}",
                    "v1:" + "a" * 64,
                    _NOW,
                    _NOW,
                    _NOW,
                ),
            )
            connection.execute(
                "INSERT INTO jobs(public_id,upload_session_id,secret_hmac,status,"
                "pipeline_version,job_type,language,expires_at,input_file_count,"
                "created_at,updated_at) VALUES(?,?,?,'running','v1','audio','fr',"
                "?,0,?,?)",
                (
                    f"job_000000000000{number}",
                    number,
                    "v1:" + "a" * 64,
                    _NOW,
                    _NOW,
                    _NOW,
                ),
            )
            connection.execute(
                "INSERT INTO job_attempts(job_id,attempt_number,status) "
                "VALUES(?,1,'running')",
                (number,),
            )
            connection.execute(
                "INSERT INTO job_run_snapshots(job_id,attempt_number,request_json,"
                "claimed_at,worker_token) VALUES(?,1,'{}',?,?)",
                (number, _NOW, f"{_TOKEN}{number}"),
            )
    return JobService(database), database


def _attempt(**updates: object) -> UsageAttempt:
    values: dict[str, object] = {
        "attempt_id": "pa_abcdefghijklmnop",
        "operation_family": "llm",
        "provider": "api",
        "model": "gpt-5.4",
        "status": "success",
        "started_at": _NOW,
        "finished_at": "2026-07-18T10:00:01Z",
        "input_tokens": 12,
        "output_tokens": 4,
        "cache_tokens": 3,
        "duration_ms": 1_000,
        "cost_micro_eur": 7,
        "cost_source": "configured",
        "native_cost_micros": 8,
        "native_currency": "USD",
        "conversion_rate": "0.875",
    }
    values.update(updates)
    return UsageAttempt(**values)  # type: ignore[arg-type]


def _payload(
    attempt: UsageAttempt,
    revision: int = 1,
    *,
    job: int = 1,
    token: str | None = None,
) -> dict[str, object]:
    return IpcMessage(
        f"job_000000000000{job}",
        1,
        token or f"{_TOKEN}{job}",
        attempt.to_runner_event(revision),
    ).payload()


@pytest.mark.parametrize("status", ["success", "failed", "cancelled", "timed_out"])
def test_provider_status_is_persisted_and_aggregated_once(
    tmp_path: Path, status: str
) -> None:
    service, database = _service(tmp_path)
    attempt = _attempt(status=status)

    assert service.apply_ipc(_payload(attempt))
    assert not service.apply_ipc(_payload(attempt))
    assert service.apply_ipc(_payload(attempt, 2))

    with database.transaction() as connection:
        rows = connection.execute(
            "SELECT status,input_tokens,output_tokens,cache_tokens,cost_micro_eur,"
            "native_cost_micros,native_currency,conversion_rate "
            "FROM provider_usage_attempts"
        ).fetchall()
        aggregate = connection.execute(
            "SELECT input_tokens,output_tokens,provider_cost_micro_eur,cost_known "
            "FROM job_attempts WHERE job_id=1 AND attempt_number=1"
        ).fetchone()
    assert [tuple(row) for row in rows] == [(status, 12, 4, 3, 7, 8, "USD", "0.875")]
    assert tuple(aggregate) == (15, 4, 7, 0)


def test_invalid_usage_does_not_advance_revision_or_write_event(
    tmp_path: Path,
) -> None:
    service, database = _service(tmp_path)
    valid = _attempt().to_runner_event(1)
    parameters = dict(valid.parameters)
    parameters.pop("provider")
    parameters["error"] = "secret provider path"
    hostile = RunnerEvent(
        CONTRACT_VERSION,
        EventType.USAGE_RECORDED,
        1,
        parameters=parameters,
    )
    payload = IpcMessage("job_0000000000001", 1, f"{_TOKEN}1", hostile).payload()

    assert not service.apply_ipc(payload)
    with database.transaction() as connection:
        revision = connection.execute(
            "SELECT last_event_revision FROM job_run_snapshots WHERE job_id=1"
        ).fetchone()[0]
        attempts = connection.execute(
            "SELECT COUNT(*) FROM provider_usage_attempts"
        ).fetchone()[0]
        events = connection.execute("SELECT COUNT(*) FROM job_run_events").fetchone()[0]
    assert (revision, attempts, events) == (-1, 0, 0)


def test_wrong_worker_is_rejected_without_persistence(tmp_path: Path) -> None:
    service, database = _service(tmp_path)

    assert not service.apply_ipc(_payload(_attempt(), token="wrong_" + "x" * 32))
    with database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM provider_usage_attempts"
            ).fetchone()[0]
            == 0
        )


def test_duplicate_attempt_id_never_aggregates_across_jobs(tmp_path: Path) -> None:
    service, database = _service(tmp_path, jobs=2)
    attempt = _attempt()

    assert service.apply_ipc(_payload(attempt, job=1))
    assert service.apply_ipc(_payload(attempt, job=2))

    with database.transaction() as connection:
        rows = connection.execute(
            "SELECT job_id,attempt_id FROM provider_usage_attempts"
        ).fetchall()
        totals = connection.execute(
            "SELECT job_id,input_tokens,output_tokens,provider_cost_micro_eur "
            "FROM job_attempts ORDER BY job_id"
        ).fetchall()
    assert [tuple(row) for row in rows] == [(1, attempt.attempt_id)]
    assert [tuple(row) for row in totals] == [(1, 15, 4, 7), (2, 0, 0, 0)]


def test_terminal_attempt_marks_mixed_cost_as_partial(tmp_path: Path) -> None:
    service, database = _service(tmp_path)
    known = _attempt()
    unknown = _attempt(
        attempt_id="pa_qrstuvwxyzabcdef",
        cost_micro_eur=None,
        cost_source="unavailable",
        native_cost_micros=None,
        native_currency=None,
        conversion_rate=None,
    )

    assert service.apply_ipc(_payload(known, 1))
    assert service.apply_ipc(_payload(unknown, 2))
    assert service.worker_lost("job_0000000000001", 1, f"{_TOKEN}1", "failed")

    with database.transaction() as connection:
        aggregate = connection.execute(
            "SELECT provider_cost_micro_eur,has_known_cost,cost_known "
            "FROM job_attempts WHERE job_id=1 AND attempt_number=1"
        ).fetchone()
    assert tuple(aggregate) == (7, 1, 0)


def test_terminal_attempt_without_provider_calls_has_complete_zero_cost(
    tmp_path: Path,
) -> None:
    service, database = _service(tmp_path)

    assert service.worker_lost("job_0000000000001", 1, f"{_TOKEN}1", "failed")

    with database.transaction() as connection:
        aggregate = connection.execute(
            "SELECT provider_cost_micro_eur,has_known_cost,cost_known "
            "FROM job_attempts WHERE job_id=1 AND attempt_number=1"
        ).fetchone()
    assert tuple(aggregate) == (0, 0, 1)


def test_worker_loss_runs_terminal_cleanup_after_commit(tmp_path: Path) -> None:
    _, database = _service(tmp_path)
    calls: list[str] = []
    service = JobService(
        database,
        terminal_cleanup=lambda job_id: calls.append(job_id) or True,
    )

    assert service.worker_lost(
        "job_0000000000001", 1, f"{_TOKEN}1", "failed"
    )
    assert calls == ["job_0000000000001"]


def test_failed_provider_usage_updates_the_persistent_circuit(tmp_path: Path) -> None:
    _, database = _service(tmp_path)
    breaker = CircuitBreaker(database, failure_threshold=2, open_seconds=60)
    service = JobService(database, circuit_breaker=breaker)
    assert service.apply_ipc(
        _payload(_attempt(status="failed", attempt_id="pa_failure_attempt01"), 1)
    )
    assert service.apply_ipc(
        _payload(_attempt(status="failed", attempt_id="pa_failure_attempt02"), 2)
    )
    assert not breaker.allow("api", "llm")


def test_real_failure_updates_only_failure_history(tmp_path: Path) -> None:
    _, database = _service(tmp_path)
    service = JobService(database, record_metrics=True)
    assert service.worker_lost("job_0000000000001", 1, f"{_TOKEN}1", "failed")
    with database.transaction() as connection:
        failures = connection.execute(
            "SELECT final_status,job_count FROM job_failure_metrics"
        ).fetchall()
        successes = connection.execute("SELECT COUNT(*) FROM job_metrics").fetchone()[0]
    assert [tuple(row) for row in failures] == [("failed", 1)]
    assert successes == 0


def test_progress_updates_do_not_move_backwards_within_one_stage(
    tmp_path: Path,
) -> None:
    service, database = _service(tmp_path)
    for revision, ratio in ((1, 0.8), (2, 0.2)):
        event = RunnerEvent(
            CONTRACT_VERSION,
            EventType.STAGE_PROGRESS,
            revision,
            stage_code=StageCode.TRANSCRIPTION,
            current_ratio=ratio,
            overall_ratio=ratio,
        )
        assert service.apply_ipc(
            IpcMessage(
                "job_0000000000001", 1, f"{_TOKEN}1", event
            ).payload()
        )
    with database.transaction() as connection:
        progress = connection.execute(
            "SELECT stage_progress_milli,total_progress_milli FROM jobs WHERE id=1"
        ).fetchone()
    assert tuple(progress) == (800, 800)
