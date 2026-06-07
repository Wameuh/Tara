"""Transcription server integration and merged transcription processing."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from tara.analysis.models import (
    MergedTranscription,
    SegmentAuthor,
    TranscriptionSegment,
)
from tara.config import TaraConfig

LOGGER = logging.getLogger(__name__)
MAX_MERGED_SEGMENT_TEXT_CHARS = 12_000


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


class InferenceServerClient:
    """Synchronous client for the OpenAI-compatible inference server."""

    def __init__(self, base_url: str, model: str, timeout: int) -> None:
        """Initialize the client."""
        self._endpoint = f"{base_url.rstrip('/')}/v1/audio/transcriptions"
        self._model = model
        self._timeout = timeout

    def transcribe_file(
        self,
        file_path: Path,
        *,
        language: str | None,
        stream: bool,
    ) -> TranscriptionResponse:
        """Transcribe one audio file.

        Args:
            file_path: Audio file path.
            language: Optional language hint.
            stream: Whether to request server-sent progress events.

        Returns:
            Parsed transcription response.
        """
        data = {"model": self._model, "response_format": "json"}
        if language:
            data["language"] = language
        if stream:
            data["stream"] = "true"
        headers = {"Accept": "text/event-stream"} if stream else None
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
            return self._parse_streaming_response(response, language=language)
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("Transcription response must be a JSON object.")
        return _response_from_mapping(payload, fallback_language=language)

    def _parse_streaming_response(
        self,
        response: requests.Response,
        *,
        language: str | None,
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
                elif message_type == "final":
                    final_payload = payload
                    break
                elif message_type == "error":
                    raise ValueError(str(payload.get("message", "Streaming error")))
        finally:
            response.close()
        if final_payload is None:
            raise ValueError("Streaming response ended without a final payload.")
        parsed = _response_from_mapping(final_payload, fallback_language=language)
        if not parsed.segments and segments:
            return TranscriptionResponse(
                text=parsed.text or " ".join(segment.text for segment in segments),
                segments=segments,
                language=parsed.language,
                duration=parsed.duration,
                model=parsed.model,
            )
        return parsed


def transcribe_audio_directory(
    audio_dir: Path,
    config: TaraConfig,
) -> list[TranscriptionFileResult]:
    """Transcribe all configured audio files under a directory."""
    client = InferenceServerClient(
        base_url=config.transcription.inference_endpoint,
        model=config.transcription.model,
        timeout=config.transcription.request_timeout_seconds,
    )
    audio_files = discover_audio_files(
        audio_dir=audio_dir,
        extensions=config.transcription.audio_extensions,
        recursive=config.transcription.recursive,
    )
    results: list[TranscriptionFileResult] = []
    output_dir = audio_dir / config.transcription.output_dir
    for audio_path in audio_files:
        started_at = time.monotonic()
        response = client.transcribe_file(
            audio_path,
            language=config.language,
            stream=config.transcription.streaming_enabled,
        )
        output_path = (
            output_dir / audio_path.relative_to(audio_dir).with_suffix(".json")
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(response.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.append(
            TranscriptionFileResult(
                file_path=audio_path,
                output_path=output_path,
                duration_seconds=response.duration,
                processing_time_seconds=time.monotonic() - started_at,
            ),
        )
    return results


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
        path
        for path in candidates
        if path.is_file() and path.suffix.lower() in allowed
    )


def process_transcriptions(output_dir: Path, config: TaraConfig) -> Path | None:
    """Merge transcription JSON files into the canonical analysis input."""
    if not config.processing.enabled:
        return None
    output_filename = config.processing.output_filename
    output_path = output_dir / output_filename
    transcription_files = [
        path
        for path in output_dir.rglob("*.json")
        if _is_transcription_source_file(path, output_path)
    ]
    transcription_files.sort()
    if not transcription_files:
        return None

    segments: list[TranscriptionSegment] = []
    languages: set[str] = set()
    models: set[str] = set()
    durations: list[float] = []
    for file_path in transcription_files:
        loaded = _load_transcription_file(file_path)
        author = SegmentAuthor(
            speaker=_speaker_from_source_file(file_path),
            source_file=file_path.name,
        )
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

    merged_segments = sorted(
        segments,
        key=lambda segment: (
            segment.start,
            segment.end,
            segment.author.source_file if segment.author else "",
        ),
    )
    merged = MergedTranscription(
        text=_render_merged_text(merged_segments),
        segments=merged_segments,
        language=_single_value(languages),
        duration=max(
            [segment.end for segment in merged_segments] + durations + [0.0],
        ),
        model=_single_value(models),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(merged.to_dict(), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return output_path


@dataclass(frozen=True, slots=True)
class _LoadedTranscription:
    """Internal loaded transcription representation."""

    segments: list[TranscriptionSegment]
    language: str | None
    duration: float | None
    model: str | None


def _load_transcription_file(path: Path) -> _LoadedTranscription:
    """Load one transcription JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"Transcription file must contain a JSON object: {path}")
    response = _response_from_mapping(raw, fallback_language=None)
    return _LoadedTranscription(
        segments=response.segments,
        language=response.language,
        duration=response.duration,
        model=response.model,
    )


def _is_transcription_source_file(path: Path, output_path: Path) -> bool:
    """Return whether a JSON file is a source transcription artifact."""
    if not path.is_file():
        return False
    if path.name == output_path.name:
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
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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
    """Parse a transcription response from a JSON mapping."""
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


def _single_value(values: set[str]) -> str | None:
    """Return one stable value from a set."""
    if not values:
        return None
    if len(values) == 1:
        return next(iter(values))
    return ",".join(sorted(values))
