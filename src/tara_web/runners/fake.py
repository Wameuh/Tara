"""Deterministic contract runner, intentionally free of Tara and SQLite imports."""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass, replace
from pathlib import Path

from tara.web_contracts import (
    CONTRACT_VERSION,
    ArtifactType,
    CancellationToken,
    ErrorCode,
    EventSink,
    EventType,
    RunnerArtifact,
    RunnerEvent,
    RunnerRequest,
    RunnerResult,
    RunnerStatus,
    StageCode,
)

STAGES = tuple(item for item in StageCode if item != StageCode.QUEUED)
_FINAL_YAML = b"""schema_name: tara.public_result
schema_version: 26.0.1
metadata:
  producer: tara-web-fake
content:
  title: Tara analysis
  sections:
    - section_id: overview
      section_type: overview
      title: Tara analysis
      blocks:
        - type: paragraph
          text: Tara analysis
"""


@dataclass(frozen=True)
class FakeRunner:
    mode: str = "success"
    step_seconds: float = 0.001
    flood_events: int = 5_000

    def run(
        self,
        request: RunnerRequest,
        event_sink: EventSink,
        cancellation_token: CancellationToken,
    ) -> RunnerResult:
        original_sink = event_sink
        sequence = 0

        class SequencedSink:
            def emit(self, event: RunnerEvent) -> None:
                nonlocal sequence
                sequence += 1
                original_sink.emit(replace(event, revision=sequence))

        sink = SequencedSink()
        if self.mode == "crash":
            os._exit(17)
        for index, stage in enumerate(STAGES, 1):
            if cancellation_token.is_cancelled():
                return self._cancelled(sink, index, stage)
            sink.emit(
                RunnerEvent(
                    CONTRACT_VERSION,
                    EventType.STAGE_STARTED,
                    index,
                    stage_code=stage,
                    overall_ratio=(index - 1) / len(STAGES),
                )
            )
            for progress in (0.25, 0.75, 1.0):
                if self.mode == "blocking":
                    while not cancellation_token.is_cancelled():
                        time.sleep(self.step_seconds)
                if cancellation_token.is_cancelled():
                    return self._cancelled(sink, index, stage)
                sink.emit(
                    RunnerEvent(
                        CONTRACT_VERSION,
                        EventType.STAGE_PROGRESS,
                        index,
                        stage_code=stage,
                        substage_code="working",
                        current_ratio=progress,
                        overall_ratio=((index - 1) + progress) / len(STAGES),
                    )
                )
                time.sleep(self.step_seconds)
            if self.mode == "retry" and index == 2:
                sink.emit(
                    RunnerEvent(
                        CONTRACT_VERSION,
                        EventType.RETRY_SCHEDULED,
                        index,
                        stage_code=stage,
                    )
                )
            if self.mode == "failure" and index == 3:
                result = RunnerResult(
                    CONTRACT_VERSION,
                    RunnerStatus.FAILED,
                    error_code=ErrorCode.PROCESSING_FAILED,
                )
                return self._failed(sink, result)
            if self.mode == "timeout" and index == 3:
                result = RunnerResult(
                    CONTRACT_VERSION,
                    RunnerStatus.TIMED_OUT,
                    error_code=ErrorCode.TIMEOUT,
                )
                return self._failed(sink, result)
            if self.mode == "flood":
                for flood_index in range(self.flood_events):
                    sink.emit(
                        RunnerEvent(
                            CONTRACT_VERSION,
                            EventType.STAGE_PROGRESS,
                            flood_index,
                            stage_code=stage,
                            substage_code="working",
                            current_ratio=1.0,
                            overall_ratio=index / len(STAGES),
                        )
                    )
            sink.emit(
                RunnerEvent(
                    CONTRACT_VERSION, EventType.STAGE_COMPLETED, index, stage_code=stage
                )
            )
        if self.mode == "missing":
            return self._completed(sink)
        work = Path("work")
        work.mkdir(mode=0o700, exist_ok=True)
        if self.mode == "corrupt":
            self._write_final(work, b"- invalid\n")
        elif self.mode == "oversized":
            self._write_final(work, b"x" * (2 * 1024 * 1024))
        else:
            self._write_final(work, _FINAL_YAML)
        return self._completed(sink)

    @staticmethod
    def _cancelled(sink: EventSink, index: int, stage: StageCode) -> RunnerResult:
        sink.emit(
            RunnerEvent(
                CONTRACT_VERSION,
                EventType.CANCELLATION_ACKNOWLEDGED,
                index,
                stage_code=stage,
            )
        )
        return RunnerResult(CONTRACT_VERSION, RunnerStatus.CANCELLED)

    @staticmethod
    def _completed(sink: EventSink) -> RunnerResult:
        sink.emit(
            RunnerEvent(
                CONTRACT_VERSION,
                EventType.ARTIFACT_DECLARED,
                len(STAGES),
                code=ArtifactType.FINAL_YAML,
            )
        )
        sink.emit(RunnerEvent(CONTRACT_VERSION, EventType.RUN_COMPLETED, len(STAGES)))
        return RunnerResult(
            CONTRACT_VERSION,
            RunnerStatus.COMPLETED,
            artifacts=(
                RunnerArtifact(ArtifactType.FINAL_YAML, "work/final.yaml", True),
            ),
        )

    @staticmethod
    def _failed(sink: EventSink, result: RunnerResult) -> RunnerResult:
        sink.emit(
            RunnerEvent(
                CONTRACT_VERSION,
                EventType.RUN_FAILED,
                len(STAGES),
                code=result.error_code,
            )
        )
        return result

    @staticmethod
    def _write_final(work: Path, content: bytes) -> None:
        target = work / "final.yaml"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(target, flags, 0o600)
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(descriptor, content[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if os.name != "nt":
            directory = os.open(work, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                if not stat.S_ISDIR(os.fstat(directory).st_mode):
                    raise OSError("work directory is invalid")
                os.fsync(directory)
            finally:
                os.close(directory)
