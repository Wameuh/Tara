"""Tests for Modal map-based transcription client helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tara.config import TaraConfig
from tara.modal_transcription import (
    _ActiveModalCall,
    _average_progress,
    _job_is_processing,
    _job_wait_deadline,
    _modal_cost_snapshot,
    _resolve_modal_spawn_parallelism,
    _upload_audio_reference,
    _validate_result_payload,
    transcribe_audio_directory_via_map,
)
from tara.transcription import TranscriptionDirectoryResult


@dataclass(frozen=True)
class _FakeJob:
    """Minimal job stand-in for helper tests."""

    file_index: int
    relative_path: str


@pytest.mark.parametrize(
    ("total_files", "expected"),
    [
        (1, 1),
        (2, 1),
        (3, 2),
        (6, 3),
        (20, 3),
        (25, 3),
    ],
)
def test_resolve_modal_spawn_parallelism_uses_files_per_container_ratio(
    total_files: int,
    expected: int,
) -> None:
    """Return the expected concurrent container count for planning/logging."""
    assert (
        _resolve_modal_spawn_parallelism(
            total_files,
            configured_parallelism=0,
            max_containers=3,
            files_per_container=2,
        )
        == expected
    )


def test_resolve_modal_spawn_parallelism_respects_explicit_parallelism_cap() -> None:
    """An explicit parallelism cap lowers the expected concurrency estimate."""
    assert (
        _resolve_modal_spawn_parallelism(
            6,
            configured_parallelism=2,
            max_containers=3,
            files_per_container=2,
        )
        == 2
    )


def test_job_wait_deadline_separates_queue_and_processing_timeouts() -> None:
    """Queue wait and active transcription use independent timeout budgets."""
    spawned_at = 100.0
    assert (
        _job_wait_deadline(
            spawned_at=spawned_at,
            processing_started_at=None,
            queue_grace_seconds=600,
            request_timeout_seconds=1800,
        )
        == 700.0
    )
    assert (
        _job_wait_deadline(
            spawned_at=spawned_at,
            processing_started_at=500.0,
            queue_grace_seconds=600,
            request_timeout_seconds=1800,
        )
        == 2300.0
    )


def test_job_is_processing_detects_running_status() -> None:
    """Progress dict entries in running state start the processing timeout."""
    assert _job_is_processing({"status": "starting", "progress_pct": 0.0}) is False
    assert _job_is_processing({"status": "running", "progress_pct": 12.5}) is True


def test_referenced_upload_accepts_more_than_old_inline_limit_without_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio_file = tmp_path / "large.ogg"
    with audio_file.open("wb") as handle:
        handle.seek(100 * 1024 * 1024)
        handle.write(b"x")
    uploaded: list[tuple[str, str]] = []

    class Batch:
        def __enter__(self) -> Batch:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def put_file(self, local: str, remote: str) -> None:
            uploaded.append((local, remote))

    class Volume:
        def batch_upload(self, *, force: bool = False) -> Batch:
            return Batch()

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _: (_ for _ in ()).throw(AssertionError("read_bytes forbidden")),
    )
    remote, size, digest = _upload_audio_reference(
        Volume(),
        audio_file,
        "run_0000000000001",
        max_bytes=1024 * 1024 * 1024,
    )

    assert size == 100 * 1024 * 1024 + 1
    assert len(digest) == 64
    assert uploaded == [(str(audio_file), remote)]


def test_average_progress_counts_missing_files_as_zero() -> None:
    """Average progress treats missing and incomplete files as 0%."""
    states = {
        1: {"status": "complete", "progress_pct": 100.0},
        2: {"status": "running", "progress_pct": 50.0},
    }
    assert _average_progress(states, total_files=4) == 37.5


def test_modal_cost_uses_only_attempt_processing_duration() -> None:
    import time
    from datetime import UTC, datetime

    config = TaraConfig()
    config.transcription.modal_usd_per_second = "0.000306"
    config.analysis.llm.usd_to_eur_rate = "0.92"
    active = _ActiveModalCall(
        job=SimpleNamespace(model="parakeet:test"),
        call=None,
        storage_path="runs/run_0000000000001/audio.ogg",
        attempt_id="pa_abcdefghijklmnop",
        started_at=datetime.now(UTC),
        started_monotonic=time.monotonic(),
        attempt_number=1,
    )

    snapshot = _modal_cost_snapshot(active, config, 0.5)

    assert snapshot is not None
    assert snapshot.native_cost_micros == 153
    assert snapshot.cost_micro_eur == 141
    assert snapshot.source == "modal_duration_estimate"


def test_modal_result_rejects_parent_traversal() -> None:
    hostile = SimpleNamespace(payload={"text": "ok", "segments": []})
    hostile.relative_path = "../outside.yaml"

    with pytest.raises(ValueError, match="invalid"):
        _validate_result_payload(hostile)


def test_transcribe_audio_directory_via_map_collects_modal_task_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map transcription collects task IDs and writes JSON outputs."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "1-a.wav").write_bytes(b"audio-a")
    (audio_dir / "2-b.wav").write_bytes(b"audio-b")
    (audio_dir / "3-c.wav").write_bytes(b"audio-c")
    active_calls = 0
    max_active_calls = 0
    spawn_calls = 0
    usage_attempts: list[Any] = []

    @dataclass(frozen=True)
    class FakeJobResult:
        file_index: int
        relative_path: str
        payload: dict[str, object]
        modal_task_id: str | None
        processing_time_seconds: float

    class FakeFunctionCall:
        def __init__(self, result: FakeJobResult) -> None:
            self._result = result

        def get(self, *, timeout: float = 0) -> FakeJobResult:
            nonlocal active_calls
            active_calls -= 1
            return self._result

    class FakeFunction:
        def spawn(self, job: Any) -> FakeFunctionCall:
            nonlocal active_calls, max_active_calls, spawn_calls
            spawn_calls += 1
            if spawn_calls == 1:
                raise RuntimeError("provider unavailable")
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            return FakeFunctionCall(
                FakeJobResult(
                    file_index=job.file_index,
                    relative_path=job.relative_path,
                    payload={
                        "text": job.relative_path,
                        "segments": [
                            {
                                "start": 0.0,
                                "end": 1.0,
                                "text": job.relative_path,
                            },
                        ],
                        "language": "fr",
                        "duration": 1.0,
                        "model": "parakeet:test",
                    },
                    modal_task_id=f"ta-{job.file_index}",
                    processing_time_seconds=0.5,
                ),
            )

    class FakeDict:
        def __init__(self) -> None:
            self._values: dict[int, dict[str, object]] = {}

        def __setitem__(self, key: int, value: dict[str, object]) -> None:
            self._values[key] = value

        def items(self) -> Any:
            return self._values.items()

        def clear(self) -> None:
            self._values.clear()

    fake_dict = FakeDict()
    removed: list[tuple[str, bool]] = []

    class FakeBatch:
        def __enter__(self) -> FakeBatch:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def put_file(self, local: str, remote: str) -> None:
            assert Path(local).is_file()
            assert remote.startswith("runs/")

    class FakeVolume:
        def batch_upload(self, *, force: bool = False) -> FakeBatch:
            assert force is False
            return FakeBatch()

        def remove_file(self, path: str, *, recursive: bool = False) -> None:
            removed.append((path, recursive))

    class FakeModalModule:
        class Dict:
            @staticmethod
            def from_name(name: str, *, create_if_missing: bool = False) -> FakeDict:
                return fake_dict

            @staticmethod
            def delete(name: str) -> None:
                return None

        class Function:
            @staticmethod
            def from_name(app_name: str, function_name: str) -> FakeFunction:
                return FakeFunction()

        class Volume:
            @staticmethod
            def from_name(name: str, *, create_if_missing: bool = False) -> FakeVolume:
                assert create_if_missing is True
                return FakeVolume()

    class FakeTranscriptionJob:
        def __init__(
            self,
            *,
            run_id: str,
            file_index: int,
            relative_path: str,
            storage_path: str,
            size_bytes: int,
            sha256_hex: str,
            expires_at: str,
            model: str,
            language: str | None,
        ) -> None:
            self.run_id = run_id
            self.file_index = file_index
            self.relative_path = relative_path
            self.storage_path = storage_path
            self.size_bytes = size_bytes
            self.sha256_hex = sha256_hex
            self.expires_at = expires_at
            self.model = model
            self.language = language

    monkeypatch.setattr(
        "tara.modal_transcription._import_modal_job_types",
        lambda: (FakeTranscriptionJob, FakeJobResult),
    )
    monkeypatch.setattr(
        "tara.modal_transcription._get_transcribe_spawn_target",
        lambda: FakeFunction(),
    )
    monkeypatch.setattr(
        "tara.modal_transcription.list_inference_container_ids",
        lambda **kwargs: ["co-existing"],
    )
    monkeypatch.setitem(__import__("sys").modules, "modal", FakeModalModule())

    config = TaraConfig()
    config.transcription.parallelism = 2
    config.transcription.modal_usd_per_second = "0.000306"
    config.analysis.llm.usd_to_eur_rate = "0.92"
    result = transcribe_audio_directory_via_map(
        audio_dir,
        config,
        usage_attempt_callback=usage_attempts.append,
    )

    assert isinstance(result, TranscriptionDirectoryResult)
    assert result.modal_container_ids == frozenset({"ta-1", "ta-2", "ta-3"})
    assert result.run_start_container_ids == frozenset({"co-existing"})
    assert len(result.file_results) == 3
    assert max_active_calls == 2
    assert [attempt.status for attempt in usage_attempts].count("failed") == 1
    assert [attempt.status for attempt in usage_attempts].count("success") == 3
    assert len({attempt.attempt_id for attempt in usage_attempts}) == 4
    assert all(
        attempt.cost_source == "modal_duration_estimate"
        for attempt in usage_attempts
        if attempt.status == "success"
    )
    assert (audio_dir / "transcriptions" / "1-a.yaml").exists()
    assert (audio_dir / "transcriptions" / "2-b.yaml").exists()
    assert (audio_dir / "transcriptions" / "3-c.yaml").exists()
    assert any(recursive for _, recursive in removed)


def test_transcribe_audio_directory_routes_to_modal_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transcription router delegates to the Modal map client."""
    from tara.transcription import transcribe_audio_directory

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    routed: list[Path] = []

    def fake_map(
        audio_path: Path,
        config: TaraConfig,
        **callbacks: object,
    ) -> TranscriptionDirectoryResult:
        routed.append(audio_path)
        return TranscriptionDirectoryResult(
            file_results=[], modal_container_ids=frozenset()
        )

    monkeypatch.setattr(
        "tara.modal_transcription.transcribe_audio_directory_via_map",
        fake_map,
    )
    config = TaraConfig()
    config.transcription.inference_auth_provider = "modal_map"
    transcribe_audio_directory(audio_dir, config)
    assert routed == [audio_dir]
