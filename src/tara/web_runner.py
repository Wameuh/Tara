"""Managed Tara implementation of the versioned web-worker contract.

This adapter deliberately owns all filesystem translation between a job
workspace and the standalone pipeline.  CLI callers keep using
``TaraControlAgent`` directly and therefore retain their historical behavior.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Lock
from typing import Any

from tara.cli import TaraArgs
from tara.config import TaraConfig, load_config
from tara.pipeline import TaraControlAgent, TaraPipelineError
from tara.prompt_security import PromptSecurityRejected, PromptSecurityUnavailable
from tara.schemas.registry import load_merged_transcription, load_public_result
from tara.token_limits import require_token_limit
from tara.web_contracts import (
    CONTRACT_VERSION,
    ArtifactType,
    CancellationToken,
    ErrorCode,
    EventSink,
    EventType,
    RunnerArtifact,
    RunnerEvent,
    RunnerMetrics,
    RunnerRequest,
    RunnerResult,
    RunnerStatus,
    StageCode,
)
from tara.yaml_utils import load_yaml_or_json

_MANIFEST_MAX_BYTES = 262_144
_CONTEXT_MAX_BYTES = 200_000
_PREVIOUS_SUMMARIES_MAX_BYTES = 2_000_000
_MANIFEST_FIELDS = {
    "path",
    "person",
    "display_name",
    "mime",
    "detected_type",
    "duration_ms",
    "bytes",
    "sha256",
    "source_id",
}
_AUDIO_TYPES = {"mp3", "ogg", "aac", "m4a"}
_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_STAGE_WEIGHTS: dict[str, dict[StageCode, float]] = {
    "audio": {
        StageCode.INPUT_VALIDATION: 0.02,
        StageCode.TRANSCRIPTION: 0.48,
        StageCode.SESSION_PREPARATION: 0.02,
        StageCode.NARRATIVE_ANALYSIS: 0.45,
        StageCode.SYNTHESIS: 0.02,
        StageCode.VERIFICATION: 0.005,
        StageCode.RESULT_READY: 0.005,
    },
    "merged_transcription": {
        StageCode.INPUT_VALIDATION: 0.03,
        StageCode.TRANSCRIPTION: 0.0,
        StageCode.SESSION_PREPARATION: 0.0,
        StageCode.NARRATIVE_ANALYSIS: 0.93,
        StageCode.SYNTHESIS: 0.025,
        StageCode.VERIFICATION: 0.01,
        StageCode.RESULT_READY: 0.005,
    },
}


class TaraWebRunner:
    """Execute one confined Tara job and publish only a public YAML staging file."""

    def __init__(self, config: TaraConfig | None = None) -> None:
        self._config = config

    def run(
        self,
        request: RunnerRequest,
        event_sink: EventSink,
        cancellation_token: CancellationToken,
    ) -> RunnerResult:
        sink = _SequencedSink(event_sink, input_kind=request.input_kind)
        started = time.monotonic()
        try:
            workspace = _workspace_for_request(request)
            _check_cancelled(cancellation_token, sink, StageCode.INPUT_VALIDATION)
            sink.stage_started(StageCode.INPUT_VALIDATION)
            _validate_token_inputs(request, workspace)
            args, speakers = self._prepare_args(request, workspace)
            _check_cancelled(cancellation_token, sink, StageCode.INPUT_VALIDATION)
            sink.stage_completed(StageCode.INPUT_VALIDATION)

            config = self._config or _load_server_config(request.tara_config_json)
            config.language = request.language
            agent = TaraControlAgent(
                args,
                config=config,
                event_sink=sink,
                cancellation_token=cancellation_token,
                speaker_by_transcription_name=speakers or None,
                # Token validation above is authoritative for web jobs: never
                # truncate user content after accepting it.
                context_char_limits=(None, None),
            )
            result = agent.run()
            _check_cancelled(cancellation_token, sink, StageCode.RESULT_READY)
            if result.analysis_output_dir is None:
                raise TaraPipelineError("analysis did not produce a public result")
            source = result.analysis_output_dir / "final.yaml"
            target = workspace / "work" / "final.yaml"
            _publish_staging_yaml(source, target, workspace, cancellation_token)
            metrics = _metrics_from_usage(result.analysis_output_dir, started)
            sink.emit(
                EventType.ARTIFACT_DECLARED,
                code=ArtifactType.FINAL_YAML,
            )
            sink.emit(EventType.RUN_COMPLETED)
            return RunnerResult(
                CONTRACT_VERSION,
                RunnerStatus.COMPLETED,
                metrics=metrics,
                artifacts=(
                    RunnerArtifact(ArtifactType.FINAL_YAML, "work/final.yaml", True),
                ),
            )
        except _Cancelled:
            return RunnerResult(CONTRACT_VERSION, RunnerStatus.CANCELLED)
        except _InputRejected:
            return _failed_result(sink, ErrorCode.INPUT_INVALID, started)
        except PromptSecurityRejected:
            return _failed_result(
                sink,
                ErrorCode.PROMPT_INJECTION_DETECTED,
                started,
            )
        except PromptSecurityUnavailable:
            return _failed_result(
                sink,
                ErrorCode.PROMPT_SECURITY_CHECK_FAILED,
                started,
            )
        except Exception:
            if cancellation_token.is_cancelled():
                sink.emit(
                    EventType.CANCELLATION_ACKNOWLEDGED,
                    stage_code=StageCode.RESULT_READY,
                )
                return RunnerResult(CONTRACT_VERSION, RunnerStatus.CANCELLED)
            # Provider messages, paths, command lines and credentials stay in
            # worker-local logs.  The orchestration boundary sees a stable code.
            code = (
                ErrorCode.TRANSCRIPTION_FAILED
                if sink.current_stage is StageCode.TRANSCRIPTION
                else ErrorCode.PROCESSING_FAILED
            )
            return _failed_result(sink, code, started)

    def _prepare_args(
        self, request: RunnerRequest, workspace: Path
    ) -> tuple[TaraArgs, dict[str, str | tuple[str, str]]]:
        if request.input_kind == "merged_transcription":
            assert request.merged_transcription_path is not None
            merged = _managed_file(workspace, request.merged_transcription_path)
            return (
                TaraArgs(
                    merged_transcription=merged,
                    context_path=_materialize_text_alias(
                        workspace, request.context_path, "context.txt"
                    ),
                    prior_context_path=_materialize_text_alias(
                        workspace, request.previous_summaries_path, "previous.md"
                    ),
                ),
                {},
            )
        assert request.source_manifest_path is not None
        manifest = _load_manifest(
            _managed_file(workspace, request.source_manifest_path), request.job_id
        )
        audio_dir = workspace / "work" / "audio"
        audio_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        speakers: dict[str, str | tuple[str, str]] = {}
        for index, item in enumerate(manifest["inputs"], start=1):
            source = _managed_file(workspace, str(item["path"]))
            _verify_file(source, int(item["bytes"]), str(item["sha256"]))
            detected = str(item["detected_type"] or "").lower()
            if detected not in _AUDIO_TYPES:
                raise _InputRejected
            destination = audio_dir / f"track-{index:04d}.{detected}"
            _link_regular(source, destination)
            speakers[destination.with_suffix(".yaml").name] = (
                str(item["person"] or f"track-{index:04d}"),
                str(item["source_id"]),
            )
        return (
            TaraArgs(
                audio_dir=audio_dir,
                context_path=_materialize_text_alias(
                    workspace, request.context_path, "context.txt"
                ),
                prior_context_path=_materialize_text_alias(
                    workspace, request.previous_summaries_path, "previous.md"
                ),
            ),
            speakers,
        )


class _SequencedSink:
    def __init__(self, target: EventSink, *, input_kind: str = "audio") -> None:
        self._target = target
        self._input_kind = input_kind
        self._revision = 0
        self._lock = Lock()
        self._current_stage: StageCode | None = None

    @property
    def current_stage(self) -> StageCode | None:
        with self._lock:
            return self._current_stage

    def emit(
        self,
        event: RunnerEvent | EventType,
        *,
        stage_code: StageCode | None = None,
        current_ratio: float | None = None,
        code: ArtifactType | ErrorCode | None = None,
    ) -> None:
        with self._lock:
            if isinstance(event, RunnerEvent):
                if event.stage_code is not None:
                    self._current_stage = event.stage_code
                self._revision += 1
                self._target.emit(
                    replace(
                        event,
                        revision=self._revision,
                        overall_ratio=_overall_ratio(
                            event.stage_code,
                            event.current_ratio,
                            self._input_kind,
                        ),
                    )
                )
                return
            if stage_code is not None:
                self._current_stage = stage_code
            self._revision += 1
            self._target.emit(
                RunnerEvent(
                    CONTRACT_VERSION,
                    event,
                    self._revision,
                    stage_code=stage_code,
                    current_ratio=current_ratio,
                    overall_ratio=_overall_ratio(
                        stage_code,
                        current_ratio,
                        self._input_kind,
                    ),
                    code=code,
                )
            )

    def stage_started(self, stage: StageCode) -> None:
        self.emit(EventType.STAGE_STARTED, stage_code=stage, current_ratio=0.0)

    def stage_completed(self, stage: StageCode) -> None:
        self.emit(EventType.STAGE_COMPLETED, stage_code=stage, current_ratio=1.0)


class _InputRejected(Exception):
    pass


class _Cancelled(Exception):
    pass


def _workspace_for_request(request: RunnerRequest) -> Path:
    workspace = Path.cwd().resolve(strict=True)
    if (
        workspace.name != request.job_id
        or workspace.is_symlink()
        or not workspace.is_dir()
    ):
        raise _InputRejected
    return workspace


def _managed_file(workspace: Path, relative_path: str) -> Path:
    _assert_no_reparse_path(workspace, relative_path)
    try:
        candidate = (workspace / relative_path).resolve(strict=True)
    except OSError as exc:
        raise _InputRejected from exc
    if not candidate.is_relative_to(workspace) or candidate.is_symlink():
        raise _InputRejected
    try:
        if not stat.S_ISREG(candidate.stat().st_mode):
            raise _InputRejected
    except OSError as exc:
        raise _InputRejected from exc
    return candidate


def _assert_no_reparse_path(workspace: Path, relative_path: str) -> None:
    """Reject links before resolution, including links to an internal target."""
    current = workspace
    for component in Path(relative_path).parts:
        current = current / component
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise _InputRejected from exc
        if stat.S_ISLNK(info.st_mode) or _is_reparse_point(current):
            raise _InputRejected


def _is_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError as exc:
        raise _InputRejected from exc


def _optional_managed_file(workspace: Path, relative_path: str | None) -> Path | None:
    return _managed_file(workspace, relative_path) if relative_path else None


def _load_manifest(path: Path, job_id: str) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MANIFEST_MAX_BYTES:
            raise _InputRejected
        with path.open("rb") as handle:
            raw = handle.read(_MANIFEST_MAX_BYTES + 1)
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _InputRejected from exc
    if len(raw) > _MANIFEST_MAX_BYTES or not isinstance(data, dict):
        raise _InputRejected
    if set(data) != {"version", "job_id", "inputs"} or data["version"] != 1:
        raise _InputRejected
    if data["job_id"] != job_id or not isinstance(data["inputs"], list):
        raise _InputRejected
    if not 1 <= len(data["inputs"]) <= 1_000:
        raise _InputRejected
    for item in data["inputs"]:
        if not isinstance(item, dict) or set(item) != _MANIFEST_FIELDS:
            raise _InputRejected
        if (
            not isinstance(item["path"], str)
            or len(item["path"]) != len("inputs/") + 32 + len(".bin")
            or not item["path"].startswith("inputs/")
            or not item["path"].endswith(".bin")
            or any(char not in "0123456789abcdef" for char in item["path"][7:-4])
        ):
            raise _InputRejected
        if not isinstance(item["bytes"], int) or isinstance(item["bytes"], bool):
            raise _InputRejected
        if not isinstance(item["sha256"], str) or len(item["sha256"]) != 64:
            raise _InputRejected
        if not isinstance(item["source_id"], str) or not _SOURCE_ID.fullmatch(
            item["source_id"]
        ):
            raise _InputRejected
    return data


def _verify_file(path: Path, expected_bytes: int, expected_sha256: str) -> None:
    if expected_bytes < 1 or expected_bytes > 1_073_741_824:
        raise _InputRejected
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(65_536):
                digest.update(chunk)
            if handle.tell() != expected_bytes:
                raise _InputRejected
    except OSError as exc:
        raise _InputRejected from exc
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        raise _InputRejected


def _link_regular(source: Path, destination: Path) -> None:
    _regular_no_follow(source)
    try:
        os.lstat(destination)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise _InputRejected from exc
    else:
        _regular_no_follow(destination)
        try:
            if source.samefile(destination):
                return
        except OSError as exc:
            raise _InputRejected from exc
        raise _InputRejected
    try:
        os.link(source, destination)
    except OSError as exc:
        # Inputs and work share the job directory. A hard link avoids an
        # unbounded duplicate of user audio and makes cross-device surprises a
        # deterministic failure instead of a silent copy.
        raise _InputRejected from exc


def _regular_no_follow(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise _InputRejected from exc
    if stat.S_ISLNK(info.st_mode) or _is_reparse_point(path):
        raise _InputRejected
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise _InputRejected
    except OSError as exc:
        raise _InputRejected from exc


def _materialize_text_alias(
    workspace: Path, relative_path: str | None, filename: str
) -> Path | None:
    """Give Tara an extension it accepts without duplicating managed input bytes."""
    if relative_path is None:
        return None
    source = _managed_file(workspace, relative_path)
    destination = workspace / "work" / filename
    try:
        destination.parent.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise _InputRejected from exc
    _assert_no_reparse_path(workspace, "work")
    _link_regular(source, destination)
    return destination


def _validate_token_inputs(request: RunnerRequest, workspace: Path) -> None:
    for relative_path, limit, maximum in (
        (request.context_path, request.limits.max_context_tokens, _CONTEXT_MAX_BYTES),
        (
            request.previous_summaries_path,
            request.limits.max_previous_summaries_tokens,
            _PREVIOUS_SUMMARIES_MAX_BYTES,
        ),
    ):
        if relative_path is None:
            continue
        path = _managed_file(workspace, relative_path)
        text = _read_text_bounded(path, maximum)
        try:
            require_token_limit(text, limit)
        except ValueError as exc:
            raise _InputRejected from exc
    if request.merged_transcription_path is not None:
        path = _managed_file(workspace, request.merged_transcription_path)
        try:
            merged = load_merged_transcription(path)
            require_token_limit(
                merged.content.text, request.limits.max_merged_transcription_tokens
            )
        except Exception as exc:
            raise _InputRejected from exc


def _read_text_bounded(path: Path, maximum: int) -> str:
    try:
        if path.stat().st_size > maximum:
            raise _InputRejected
        with path.open("rb") as handle:
            content = handle.read(maximum + 1)
        if len(content) > maximum:
            raise _InputRejected
        # Match Python text-mode universal newlines while keeping the byte
        # limit enforcement on the original managed file.
        return content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise _InputRejected from exc


def _load_server_config(snapshot_json: str | None) -> TaraConfig:
    if snapshot_json is not None:
        try:
            snapshot = json.loads(snapshot_json)
        except (TypeError, ValueError) as exc:
            raise _InputRejected from exc
        if not isinstance(snapshot, dict):
            raise _InputRejected
        return TaraConfig.from_mapping(snapshot, apply_environment=False)
    configured = os.environ.get("TARA_WEB_TARA_CONFIG")
    return load_config(Path(configured)) if configured else load_config()


def _publish_staging_yaml(
    source: Path,
    target: Path,
    workspace: Path,
    token: CancellationToken,
    *,
    before_rename: Callable[[], None] | None = None,
) -> None:
    _assert_no_reparse_path(workspace, str(source.relative_to(workspace)))
    source = source.resolve(strict=True)
    if not source.is_relative_to(workspace) or source.is_symlink():
        raise _InputRejected
    load_public_result(source)
    target.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = target.with_name(".final.yaml.tmp")
    if target.exists() or temporary.exists():
        raise _InputRejected
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            while chunk := reader.read(65_536):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if before_rename is not None:
            before_rename()
        if token.is_cancelled():
            raise _Cancelled
        os.replace(temporary, target)
        if token.is_cancelled():
            target.unlink(missing_ok=True)
            raise _Cancelled
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _metrics_from_usage(output_dir: Path, started: float) -> RunnerMetrics:
    try:
        usage = load_yaml_or_json(output_dir / "usage_report.yaml")
        if not isinstance(usage, dict):
            raise ValueError
        records = usage.get("records", [])
        if not isinstance(records, list):
            raise ValueError
        input_tokens = sum(
            int(item.get("input_tokens", 0))
            for item in records
            if isinstance(item, dict)
        )
        output_tokens = sum(
            int(item.get("output_tokens", 0))
            for item in records
            if isinstance(item, dict)
        )
    except (OSError, TypeError, ValueError):
        input_tokens = output_tokens = 0
    return RunnerMetrics(
        elapsed_seconds=max(0, round(time.monotonic() - started)),
        input_tokens=max(0, input_tokens),
        output_tokens=max(0, output_tokens),
        cost_micros=None,
    )


def _failed_result(
    sink: _SequencedSink, code: ErrorCode, started: float
) -> RunnerResult:
    sink.emit(EventType.RUN_FAILED, code=code)
    return RunnerResult(
        CONTRACT_VERSION,
        RunnerStatus.FAILED,
        metrics=RunnerMetrics(
            elapsed_seconds=max(0, round(time.monotonic() - started))
        ),
        error_code=code,
    )


def _check_cancelled(
    token: CancellationToken, sink: _SequencedSink, stage: StageCode
) -> None:
    if token.is_cancelled():
        sink.emit(EventType.CANCELLATION_ACKNOWLEDGED, stage_code=stage)
        raise _Cancelled


def _overall_ratio(
    stage: StageCode | None,
    current: float | None,
    input_kind: str = "audio",
) -> float | None:
    if stage is None or current is None or stage == StageCode.QUEUED:
        return None
    weights = _STAGE_WEIGHTS.get(input_kind, _STAGE_WEIGHTS["audio"])
    bounded = min(1.0, max(0.0, current))
    stages = tuple(weights)
    previous = sum(weights[candidate] for candidate in stages[: stages.index(stage)])
    return min(1.0, previous + (weights[stage] * bounded))
