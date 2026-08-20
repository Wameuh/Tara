from __future__ import annotations

import time
from pathlib import Path

import pytest

from tara.web_contracts import (
    CONTRACT_VERSION,
    EventType,
    RunnerEvent,
    RunnerRequest,
    StageCode,
)
from tara_web.orchestration.ipc import (
    BoundedIpcBuffer,
    IpcMessage,
    IpcViolation,
    parse_message,
)
from tara_web.orchestration.process_pool import ProcessPool


def message(revision: int, kind: EventType = EventType.STAGE_PROGRESS) -> IpcMessage:
    return IpcMessage(
        "job_000000000000",
        1,
        "x" * 43,
        RunnerEvent(
            CONTRACT_VERSION, kind, revision, stage_code=StageCode.TRANSCRIPTION
        ),
    )


def test_progress_is_the_only_coalesced_event() -> None:
    buffer = BoundedIpcBuffer(16)
    for revision in range(2_000):
        assert buffer.put(message(revision))
    assert buffer.put(message(2_001, EventType.STAGE_COMPLETED))
    events = buffer.drain()
    assert any(item["event"]["event_type"] == "stage_completed" for item in events)
    assert len(events) <= 16


def test_ipc_rejects_unknown_or_oversized_envelopes() -> None:
    with pytest.raises(IpcViolation):
        parse_message({})
    payload = message(1).payload()
    payload["unknown"] = "x"
    with pytest.raises(IpcViolation):
        parse_message(payload)


def test_spawn_worker_returns_one_success_result(tmp_path: Path) -> None:
    pool = ProcessPool(1, 32)
    request = RunnerRequest(
        CONTRACT_VERSION,
        "job_000000000000",
        1,
        "audio",
        source_manifest_path="inputs/source-manifest.json",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pool.submit(request, "x" * 43, "success", workspace)
    completed = []
    for _ in range(500):
        pool.take_events()
        completed = pool.finished(timeout_seconds=10, cancellation_grace_seconds=1)
        if completed:
            break
        time.sleep(0.02)
    pool.close()
    assert len(completed) == 1
    _, result, reason = completed[0]
    assert reason is None
    assert result is not None and result.status.value == "completed"
    assert (workspace / "work" / "final.yaml").is_file()


def test_spawn_flood_coalesces_progress_and_preserves_critical_events(
    tmp_path: Path,
) -> None:
    pool = ProcessPool(1, 16)
    request = RunnerRequest(
        CONTRACT_VERSION,
        "job_000000000000",
        1,
        "audio",
        source_manifest_path="inputs/source-manifest.json",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pool.submit(request, "x" * 43, "flood", workspace)
    completed = []
    events: list[dict[str, object]] = []
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        events.extend(pool.take_events())
        completed = pool.finished(timeout_seconds=20, cancellation_grace_seconds=1)
        if completed:
            events.extend(pool.take_events())
            break
        time.sleep(0.01)
    pool.close()

    assert len(completed) == 1
    _, result, reason = completed[0]
    assert reason is None
    assert result is not None and result.status.value == "completed"
    event_types = [item["event"]["event_type"] for item in events]
    assert "run_completed" in event_types
    assert event_types.count("stage_completed") == len(tuple(StageCode)) - 1
    assert event_types.count("stage_progress") <= len(tuple(StageCode)) - 1
