"""Transcription server integration and merged transcription processing."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock
from typing import Any

import requests

from tara.config import TaraConfig
from tara.providers.costing import CostSnapshot, convert_native_cost
from tara.providers.events import UsageAttempt
from tara.schemas.merged_transcription import (
    MergedTranscription,
    MergedTranscriptionContent,
    SegmentAuthor,
    TranscriptionSegment,
)
from tara.yaml_utils import load_yaml_or_json, write_yaml

LOGGER = logging.getLogger(__name__)
MAX_MERGED_SEGMENT_TEXT_CHARS = 12_000
_TRANSCRIPTION_EXTENSION_PRIORITY = {".yaml": 0, ".yml": 1, ".json": 2}
TranscriptionProgressCallback = Callable[
    [TranscriptionSegment, Mapping[str, Any]],
    None,
]
DirectoryProgressCallback = Callable[[int, int, float], None]
CancellationCheck = Callable[[], None]
UsageAttemptCallback = Callable[[UsageAttempt], None]


def _modal_proxy_cost_snapshot(
    config: TaraConfig,
    duration_seconds: float,
) -> CostSnapshot | None:
    """Estimate a proxied Modal request from its measured request duration."""
    if config.transcription.inference_auth_provider.strip().lower() != "modal_proxy":
        return None
    native_rate = config.transcription.modal_usd_per_second
    eur_rate = config.analysis.llm.usd_to_eur_rate
    if native_rate is None or eur_rate is None:
        return None
    try:
        native_cost = Decimal(native_rate) * Decimal(str(max(0.0, duration_seconds)))
    except InvalidOperation as exc:
        raise ValueError("cost configuration is invalid") from exc
    return convert_native_cost(
        native_cost,
        native_currency="USD",
        eur_per_native=eur_rate,
        source="modal_duration_estimate",
    )


@dataclass(frozen=True, slots=True)
class TranscriptionResponse:
    """Response returned by the local inference server."""

    text: str
    segments: list[TranscriptionSegment]
    language: str | None = None
    duration: float | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the response to a JSON-compatible dictionary."""
        return {
            "text": self.text,
            "segments": [segment.to_dict() for segment in self.segments],
            "language": self.language,
            "duration": self.duration,
            "model": self.model,
        }


@dataclass(frozen=True, slots=True)
class TranscriptionFileResult:
    """Local transcription artifact metadata for one audio file."""

    file_path: Path
    output_path: Path
    duration_seconds: float | None
    processing_time_seconds: float


@dataclass(frozen=True, slots=True)
class TranscriptionDirectoryResult:
    """Aggregate result for one directory transcription run."""

    file_results: list[TranscriptionFileResult]
    modal_container_ids: frozenset[str]
    run_start_container_ids: frozenset[str] = frozenset()


class InferenceServerClient:
    """Synchronous client for the OpenAI-compatible inference server."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: int,
        *,
        auth_provider: str = "none",
        modal_proxy_key_env: str = "TARA_MODAL_PROXY_AUTH_KEY",
        modal_proxy_secret_env: str = "TARA_MODAL_PROXY_AUTH_SECRET",
        bearer_token_env: str = "INFERENCE_BEARER_TOKEN",
    ) -> None:
        """Initialize the client."""
        self._endpoint = f"{base_url.rstrip('/')}/v1/audio/transcriptions"
        self._model = model
        self._timeout = timeout
        self._auth_provider = auth_provider
        self._modal_proxy_key_env = modal_proxy_key_env
        self._modal_proxy_secret_env = modal_proxy_secret_env
        self._bearer_token_env = bearer_token_env

    def transcribe_file(
        self,
        file_path: Path,
        *,
        language: str | None,
        stream: bool,
        progress_callback: TranscriptionProgressCallback | None = None,
    ) -> TranscriptionResponse:
        """Transcribe one audio file.

        Args:
            file_path: Audio file path.
            language: Optional language hint.
            stream: Whether to request server-sent progress events.
            progress_callback: Optional callback invoked for every streamed segment.

        Returns:
            Parsed transcription response.
        """
        data = {"model": self._model, "response_format": "json"}
        if language:
            data["language"] = language
        if stream:
            data["stream"] = "true"
        headers = self._build_headers(stream=stream)
        with file_path.open("rb") as audio_file:
            files = {
                "file": (
                    file_path.name,
                    audio_file,
                    "application/octet-stream",
                ),
            }
            response = requests.post(
                self._endpoint,
                files=files,
                data=data,
                headers=headers,
                stream=stream,
                timeout=self._timeout,
            )
        response.raise_for_status()
        if stream:
            return self._parse_streaming_response(
                response,
                language=language,
                progress_callback=progress_callback,
            )
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("Transcription response must be a JSON object.")
        return _response_from_mapping(payload, fallback_language=language)

    def _parse_streaming_response(
        self,
        response: requests.Response,
        *,
        language: str | None,
        progress_callback: TranscriptionProgressCallback | None,
    ) -> TranscriptionResponse:
        """Parse a server-sent event transcription response."""
        segments: list[TranscriptionSegment] = []
        final_payload: Mapping[str, Any] | None = None
        try:
            for raw_line in response.iter_lines(decode_unicode=True, chunk_size=1):
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                payload = json.loads(raw_line.removeprefix("data:").strip())
                if not isinstance(payload, Mapping):
                    continue
                message_type = payload.get("type")
                if message_type == "segment":
                    segment = _segment_from_mapping(payload)
                    if segment.text.strip():
                        segments.append(segment)
                        if progress_callback is not None:
                            progress_callback(segment, payload)
                elif message_type == "final":
                    final_payload = payload
                    break
                elif message_type == "error":
                    raise ValueError(str(payload.get("message", "Streaming error")))
        finally:
            response.close()
        if final_payload is None:
            raise ValueError("Streaming response ended without a final payload.")
        text = str(
            final_payload.get("text") or " ".join(segment.text for segment in segments)
        ).strip()
        duration = final_payload.get("duration")
        if not segments and text:
            segments = [TranscriptionSegment(start=0.0, end=0.0, text=text)]
        return TranscriptionResponse(
            text=text,
            segments=segments,
            language=_optional_str(final_payload.get("language")) or language,
            duration=float(duration) if duration is not None else None,
            model=_optional_str(final_payload.get("model")),
        )

    def _build_headers(self, *, stream: bool) -> dict[str, str] | None:
        """Build request headers without logging or exposing secret values."""
        headers: dict[str, str] = {}
        if stream:
            headers["Accept"] = "text/event-stream"
        provider = self._auth_provider.strip().lower()
        if provider in {"", "none"}:
            return headers or None
        if provider == "bearer":
            token = os.getenv(self._bearer_token_env)
            if not token:
                raise ValueError(
                    "Missing inference bearer token environment variable: "
                    + self._bearer_token_env
                )
            headers["Authorization"] = f"Bearer {token}"
            return headers
        if provider != "modal_proxy":
            raise ValueError(f"Unsupported inference auth provider: {provider}")

        key = os.getenv(self._modal_proxy_key_env)
        secret = os.getenv(self._modal_proxy_secret_env)
        missing = [
            name
            for name, value in (
                (self._modal_proxy_key_env, key),
                (self._modal_proxy_secret_env, secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Missing Modal proxy auth environment variable(s): "
                + ", ".join(missing)
            )
        headers["Modal-Key"] = str(key)
        headers["Modal-Secret"] = str(secret)
        return headers


def transcribe_audio_directory(
    audio_dir: Path,
    config: TaraConfig,
    *,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: DirectoryProgressCallback | None = None,
    usage_attempt_callback: UsageAttemptCallback | None = None,
) -> TranscriptionDirectoryResult:
    """Transcribe all configured audio files under a directory."""
    provider = config.transcription.inference_auth_provider.strip().lower()
    if provider == "modal_map":
        from tara.modal_transcription import transcribe_audio_directory_via_map

        return transcribe_audio_directory_via_map(
            audio_dir,
            config,
            cancellation_check=cancellation_check,
            progress_callback=progress_callback,
            usage_attempt_callback=usage_attempt_callback,
        )

    file_results = _transcribe_audio_directory_via_http(
        audio_dir,
        config,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
        usage_attempt_callback=usage_attempt_callback,
    )
    return TranscriptionDirectoryResult(
        file_results=file_results,
        modal_container_ids=frozenset(),
    )


def _transcribe_audio_directory_via_http(
    audio_dir: Path,
    config: TaraConfig,
    *,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: DirectoryProgressCallback | None = None,
    usage_attempt_callback: UsageAttemptCallback | None = None,
) -> list[TranscriptionFileResult]:
    """Transcribe audio files via the HTTP inference server."""
    client = InferenceServerClient(
        base_url=config.transcription.inference_endpoint,
        model=config.transcription.model,
        timeout=config.transcription.request_timeout_seconds,
        auth_provider=config.transcription.inference_auth_provider,
        modal_proxy_key_env=config.transcription.modal_proxy_key_env,
        modal_proxy_secret_env=config.transcription.modal_proxy_secret_env,
        bearer_token_env=config.transcription.bearer_token_env,
    )
    audio_files = discover_audio_files(
        audio_dir=audio_dir,
        extensions=config.transcription.audio_extensions,
        recursive=config.transcription.recursive,
    )
    output_dir = audio_dir / config.transcription.output_dir
    total_files = len(audio_files)
    configured_parallelism = int(config.transcription.parallelism)
    parallelism = (
        total_files
        if configured_parallelism <= 0 and total_files > 0
        else max(1, configured_parallelism)
    )
    if total_files > 1 and parallelism > 1:
        LOGGER.info(
            "Transcribing %s audio files with parallelism=%s.",
            total_files,
            min(parallelism, total_files),
        )
    concurrency_lock = Lock()
    active_requests = 0
    peak_requests = 0

    def request_started(file_index: int) -> None:
        nonlocal active_requests, peak_requests
        with concurrency_lock:
            active_requests += 1
            peak_requests = max(peak_requests, active_requests)
            LOGGER.info(
                "Transcription request started file=%s/%s active=%s limit=%s peak=%s.",
                file_index,
                total_files,
                active_requests,
                min(parallelism, total_files),
                peak_requests,
            )

    def request_finished(file_index: int) -> None:
        nonlocal active_requests
        with concurrency_lock:
            active_requests = max(0, active_requests - 1)
            LOGGER.info(
                "Transcription request finished file=%s/%s active=%s peak=%s.",
                file_index,
                total_files,
                active_requests,
                peak_requests,
            )

    def transcribe_one(
        file_index: int,
        audio_path: Path,
    ) -> TranscriptionFileResult:
        LOGGER.info(
            "Transcribing audio file %s/%s: %s",
            file_index,
            total_files,
            audio_path,
        )
        started_at = time.monotonic()
        segment_count = 0
        if cancellation_check is not None:
            cancellation_check()

        def log_progress(
            segment: TranscriptionSegment,
            payload: Mapping[str, Any],
        ) -> None:
            nonlocal segment_count
            if cancellation_check is not None:
                cancellation_check()
            segment_count += 1
            progress = _optional_float(payload.get("progress"))
            if progress_callback is not None:
                bounded = min(1.0, max(0.0, (progress or 0.0) / 100.0))
                progress_callback(file_index, total_files, bounded)
            progress_text = f" ({progress:.1f}%)" if progress is not None else ""
            LOGGER.info(
                "Transcription progress %s/%s %s: segment %s %.2f-%.2fs%s",
                file_index,
                total_files,
                audio_path.name,
                segment_count,
                segment.start,
                segment.end,
                progress_text,
            )

        attempt_started = datetime.now(UTC)
        attempt_monotonic = time.monotonic()
        response: TranscriptionResponse | None = None
        status = "failed"
        request_started(file_index)
        try:
            response = client.transcribe_file(
                audio_path,
                language=config.language,
                stream=config.transcription.streaming_enabled,
                progress_callback=(
                    log_progress if config.transcription.streaming_enabled else None
                ),
            )
            if cancellation_check is not None:
                try:
                    cancellation_check()
                except BaseException:
                    status = "cancelled"
                    raise
            status = "success"
        except requests.Timeout:
            status = "timed_out"
            raise
        finally:
            try:
                if usage_attempt_callback is not None:
                    finished = datetime.now(UTC)
                    duration_ms = max(
                        0,
                        int((time.monotonic() - attempt_monotonic) * 1_000),
                    )
                    cost = _modal_proxy_cost_snapshot(config, duration_ms / 1_000)
                    usage = UsageAttempt(
                        attempt_id="pa_" + secrets.token_urlsafe(18),
                        operation_family="transcription",
                        operation_name="audio_track",
                        provider=(
                            "http"
                            if config.transcription.inference_auth_provider.lower()
                            in {"", "none"}
                            else config.transcription.inference_auth_provider.lower()
                        ),
                        model=(
                            response.model
                            if response
                            else config.transcription.model
                        ),
                        status=status,
                        started_at=attempt_started.isoformat().replace("+00:00", "Z"),
                        finished_at=finished.isoformat().replace("+00:00", "Z"),
                        duration_ms=duration_ms,
                        cost_micro_eur=cost.cost_micro_eur if cost else None,
                        cost_source=cost.source if cost else "unavailable",
                        native_cost_micros=cost.native_cost_micros if cost else None,
                        native_currency=cost.native_currency if cost else None,
                        conversion_rate=cost.conversion_rate if cost else None,
                    )
                    try:
                        usage_attempt_callback(usage)
                    except Exception:
                        if status == "success":
                            raise
                        LOGGER.exception(
                            "transcription usage callback failed for a "
                            "non-success attempt"
                        )
            finally:
                request_finished(file_index)
        assert response is not None
        if progress_callback is not None:
            progress_callback(file_index, total_files, 1.0)
        output_path = output_dir / audio_path.relative_to(audio_dir).with_suffix(
            ".yaml"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(output_path, response.to_dict())
        result = TranscriptionFileResult(
            file_path=audio_path,
            output_path=output_path,
            duration_seconds=response.duration,
            processing_time_seconds=time.monotonic() - started_at,
        )
        LOGGER.info(
            "Finished transcription %s/%s: %s -> %s (%s segments, %.1fs elapsed)",
            file_index,
            total_files,
            audio_path.name,
            output_path,
            len(response.segments),
            result.processing_time_seconds,
        )
        return result

    if parallelism <= 1 or total_files <= 1:
        return [
            transcribe_one(file_index, audio_path)
            for file_index, audio_path in enumerate(audio_files, start=1)
        ]

    ordered_results: list[TranscriptionFileResult | None] = [None] * total_files
    with ThreadPoolExecutor(max_workers=min(parallelism, total_files)) as executor:
        futures = {
            executor.submit(transcribe_one, file_index, audio_path): file_index - 1
            for file_index, audio_path in enumerate(audio_files, start=1)
        }
        for future in as_completed(futures):
            ordered_results[futures[future]] = future.result()
    return [result for result in ordered_results if result is not None]


def discover_audio_files(
    audio_dir: Path,
    extensions: Iterable[str],
    *,
    recursive: bool,
) -> list[Path]:
    """Discover supported audio files under `audio_dir`."""
    allowed = {extension.lower() for extension in extensions}
    candidates = audio_dir.rglob("*") if recursive else audio_dir.glob("*")
    return sorted(
        path for path in candidates if path.is_file() and path.suffix.lower() in allowed
    )


def process_transcriptions(output_dir: Path, config: TaraConfig) -> Path | None:
    """Merge transcription YAML files into the canonical analysis input."""
    return _process_transcriptions(output_dir, config)


def process_transcriptions_with_authors(
    output_dir: Path,
    config: TaraConfig,
    *,
    speaker_by_transcription_name: Mapping[str, str | tuple[str, str]] | None = None,
) -> Path | None:
    """Merge transcription files while preserving explicit web speaker labels.

    The existing :func:`process_transcriptions` remains the CLI entry point.
    Web workers pass labels from their immutable manifest rather than deriving
    them from generated physical names.
    """
    return _process_transcriptions(
        output_dir,
        config,
        speaker_by_transcription_name=speaker_by_transcription_name,
    )


def _process_transcriptions(
    output_dir: Path,
    config: TaraConfig,
    *,
    speaker_by_transcription_name: Mapping[str, str] | None = None,
) -> Path | None:
    """Implementation shared by CLI and managed web transcription merging."""
    if not config.processing.enabled:
        return None
    output_filename = config.processing.output_filename
    output_path = output_dir / output_filename
    discovered = {
        path
        for pattern in ("*.yaml", "*.yml", "*.json")
        for path in output_dir.rglob(pattern)
        if _is_transcription_source_file(path, output_path)
    }
    transcription_files = _select_canonical_transcription_files(discovered)
    if not transcription_files:
        return None

    segments: list[TranscriptionSegment] = []
    languages: set[str] = set()
    models: set[str] = set()
    durations: list[float] = []
    speaker_by_transcription_name = speaker_by_transcription_name or {}
    for file_path in transcription_files:
        loaded = _load_transcription_file(file_path)
        mapped = speaker_by_transcription_name.get(file_path.name)
        speaker, source_file = (
            mapped
            if isinstance(mapped, tuple)
            else (mapped or _speaker_from_source_file(file_path), file_path.name)
        )
        author = SegmentAuthor(speaker=speaker, source_file=source_file)
        authored_segments = [
            segment.model_copy(update={"author": author})
            for segment in _dedupe_consecutive_segments(loaded.segments)
        ]
        for segment in authored_segments:
            segments.extend(
                _split_long_segment(
                    segment,
                    max_chars=MAX_MERGED_SEGMENT_TEXT_CHARS,
                    fallback_duration=loaded.duration,
                ),
            )
        if loaded.language:
            languages.add(loaded.language)
        if loaded.model:
            models.add(loaded.model)
        if loaded.duration is not None:
            durations.append(loaded.duration)

    merged_segments = _dedupe_segments_by_content_fingerprint(
        sorted(
            segments,
            key=lambda segment: (
                segment.start,
                segment.end,
                segment.author.source_file if segment.author else "",
            ),
        ),
    )
    merged = MergedTranscription(
        metadata={"language": _single_value(languages), "producer": "tara"},
        content=MergedTranscriptionContent(
            text=_render_merged_text(merged_segments),
            segments=merged_segments,
            duration=max(
                [segment.end for segment in merged_segments] + durations + [0.0]
            ),
            model=_single_value(models),
        ),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(output_path, merged.to_dict())
    return output_path


@dataclass(frozen=True, slots=True)
class _LoadedTranscription:
    """Internal loaded transcription representation."""

    segments: list[TranscriptionSegment]
    language: str | None
    duration: float | None
    model: str | None


def _load_transcription_file(path: Path) -> _LoadedTranscription:
    """Load one transcription YAML or legacy JSON file."""
    raw = load_yaml_or_json(path)
    if not isinstance(raw, Mapping):
        raise ValueError(f"Transcription file must contain a mapping object: {path}")
    response = _response_from_mapping(raw, fallback_language=None)
    return _LoadedTranscription(
        segments=response.segments,
        language=response.language,
        duration=response.duration,
        model=response.model,
    )


def _select_canonical_transcription_files(paths: set[Path]) -> list[Path]:
    """Pick one source file per speaker track, preferring YAML over JSON.

    When both ``1-willygorn.json`` and ``1-willygorn.yaml`` exist, only the
    canonical YAML artifact is merged so utterances are not duplicated.

    Args:
        paths: Candidate transcription source paths.

    Returns:
        One canonical path per track stem, sorted by file name.
    """
    by_track: dict[str, list[Path]] = {}
    for path in paths:
        by_track.setdefault(path.stem.lower(), []).append(path)
    selected: list[Path] = []
    for track_paths in by_track.values():
        canonical = min(
            track_paths,
            key=lambda candidate: (
                _TRANSCRIPTION_EXTENSION_PRIORITY.get(
                    candidate.suffix.lower(),
                    99,
                ),
                candidate.name.lower(),
            ),
        )
        selected.append(canonical)
    return sorted(selected, key=lambda candidate: candidate.name.lower())


def _segment_content_fingerprint(
    segment: TranscriptionSegment,
) -> tuple[str, float, float, str]:
    """Return a stable fingerprint for cross-file segment deduplication."""
    speaker = segment.author.speaker if segment.author else ""
    normalized_text = " ".join(segment.text.casefold().split())
    return (
        speaker,
        round(segment.start, 2),
        round(segment.end, 2),
        normalized_text,
    )


def _dedupe_segments_by_content_fingerprint(
    segments: list[TranscriptionSegment],
) -> list[TranscriptionSegment]:
    """Remove duplicate utterances that appear in multiple source formats."""
    deduped: list[TranscriptionSegment] = []
    seen: set[tuple[str, float, float, str]] = set()
    for segment in segments:
        fingerprint = _segment_content_fingerprint(segment)
        if not fingerprint[3]:
            continue
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(segment)
    return deduped


def _is_transcription_source_file(path: Path, output_path: Path) -> bool:
    """Return whether a YAML/JSON file is a source transcription artifact."""
    if not path.is_file():
        return False
    if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
        return False
    if path.name == output_path.name:
        return False
    if path.stem.lower() in {"merged_transcription", output_path.stem.lower()}:
        return False
    if set(path.parts) & {"analysis", "scenes"}:
        return False
    if path.stem in {
        "scene_analysis",
        "scene_descriptions",
        "session_summary",
        "session_summary_verified",
        "verification_report",
    }:
        return False
    try:
        raw = load_yaml_or_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(raw, Mapping):
        return False
    segments = raw.get("segments")
    if not isinstance(segments, list):
        return False
    return any(_looks_like_transcription_segment(segment) for segment in segments)


def _response_from_mapping(
    data: Mapping[str, Any],
    *,
    fallback_language: str | None,
) -> TranscriptionResponse:
    """Parse a transcription response from a mapping (JSON wire or YAML artifact)."""
    raw_segments = data.get("segments", [])
    segments = [
        _segment_from_mapping(segment)
        for segment in raw_segments
        if isinstance(segment, Mapping)
    ]
    text = str(data.get("text", "")).strip()
    if text and not segments:
        segments = [TranscriptionSegment(start=0.0, end=0.0, text=text)]
    duration = data.get("duration")
    return TranscriptionResponse(
        text=text,
        segments=segments,
        language=_optional_str(data.get("language")) or fallback_language,
        duration=float(duration) if duration is not None else None,
        model=_optional_str(data.get("model")),
    )


def _segment_from_mapping(data: Mapping[str, Any]) -> TranscriptionSegment:
    """Parse one transcription segment from a raw mapping."""
    return TranscriptionSegment(
        start=float(data.get("start", 0.0) or 0.0),
        end=float(data.get("end", 0.0) or 0.0),
        text=str(data.get("text", "")),
        author=_author_from_mapping(data.get("author")),
    )


def _author_from_mapping(value: Any) -> SegmentAuthor | None:
    """Parse optional author metadata from a raw segment mapping."""
    if not isinstance(value, Mapping):
        return None
    speaker = _optional_str(value.get("speaker"))
    source_file = _optional_str(value.get("source_file"))
    if speaker is None or source_file is None:
        return None
    return SegmentAuthor(speaker=speaker, source_file=source_file)


def _looks_like_transcription_segment(value: Any) -> bool:
    """Return whether a value has the required transcription segment shape."""
    if not isinstance(value, Mapping):
        return False
    return all(key in value for key in ("start", "end", "text"))


def _speaker_from_source_file(path: Path) -> str:
    """Infer a stable speaker identifier from a transcription file name."""
    stem = path.stem
    prefix, separator, suffix = stem.partition("-")
    if separator and prefix.isdecimal() and suffix:
        return suffix
    return stem


def _dedupe_consecutive_segments(
    segments: Iterable[TranscriptionSegment],
) -> list[TranscriptionSegment]:
    """Remove consecutive duplicate segment text."""
    cleaned: list[TranscriptionSegment] = []
    previous: str | None = None
    for segment in segments:
        normalized = " ".join(segment.text.casefold().split())
        if normalized and normalized == previous:
            continue
        cleaned.append(segment)
        previous = normalized
    return cleaned


def _split_long_segment(
    segment: TranscriptionSegment,
    *,
    max_chars: int,
    fallback_duration: float | None,
) -> list[TranscriptionSegment]:
    """Split oversized segment text so evidence indexing can stay bounded."""
    if len(segment.text) <= max_chars:
        return [segment]
    parts = _split_text_by_words(segment.text, max_chars=max_chars)
    if len(parts) <= 1:
        return [segment]

    start = segment.start
    end = segment.end
    if end <= start and fallback_duration is not None and fallback_duration > start:
        end = fallback_duration
    total_span = max(0.0, end - start)
    total_chars = sum(len(part) for part in parts)
    cursor = start
    split_segments: list[TranscriptionSegment] = []
    for index, part in enumerate(parts):
        if total_span > 0 and total_chars > 0:
            if index == len(parts) - 1:
                part_end = end
            else:
                part_end = cursor + total_span * (len(part) / total_chars)
        else:
            part_end = cursor
        split_segments.append(
            segment.model_copy(
                update={
                    "start": cursor,
                    "end": part_end,
                    "text": part,
                },
            ),
        )
        cursor = part_end
    return split_segments


def _split_text_by_words(text: str, *, max_chars: int) -> list[str]:
    """Split text on word boundaries while respecting a character budget."""
    words = text.split()
    if not words:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        separator = 1 if current else 0
        if current and current_len + separator + len(word) > max_chars:
            parts.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += separator + len(word)
    if current:
        parts.append(" ".join(current))
    return parts


def _render_merged_text(segments: Iterable[TranscriptionSegment]) -> str:
    """Render merged transcription text with deterministic speaker labels."""
    return "\n".join(
        _render_authored_segment_line(segment)
        for segment in segments
        if segment.text.strip()
    )


def _render_authored_segment_line(segment: TranscriptionSegment) -> str:
    """Render one merged transcription line with its source speaker."""
    speaker = segment.author.speaker if segment.author else "unknown"
    return f"[{speaker}] {' '.join(segment.text.split())}"


def _optional_str(value: Any) -> str | None:
    """Return a stripped string or `None`."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    """Return a float or `None` when progress metadata is absent."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _single_value(values: set[str]) -> str | None:
    """Return one stable value from a set."""
    if not values:
        return None
    if len(values) == 1:
        return next(iter(values))
    return ",".join(sorted(values))
