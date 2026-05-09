"""Configuration loading for the standalone Tara application."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self


@dataclass(slots=True)
class LoggingConfig:
    """Logging configuration for local and server runs."""

    level: str = "INFO"
    console_enabled: bool = True
    console_format: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create logging configuration from a raw mapping."""
        defaults = cls()
        return cls(
            level=str(data.get("level", defaults.level)),
            console_enabled=bool(data.get("console_enabled", defaults.console_enabled)),
            console_format=str(data.get("console_format", defaults.console_format)),
        )


@dataclass(slots=True)
class TranscriptionConfig:
    """Configuration for the local transcription inference server."""

    model: str = "parakeet:nvidia/parakeet-tdt-0.6b-v3"
    inference_endpoint: str = "http://localhost:8000"
    request_timeout_seconds: int = 1800
    streaming_enabled: bool = True
    output_dir: str = "transcriptions"
    recursive: bool = True
    audio_extensions: list[str] = field(
        default_factory=lambda: [
            ".mp3",
            ".wav",
            ".ogg",
            ".flac",
            ".m4a",
            ".aac",
            ".wma",
        ],
    )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create transcription configuration from a raw mapping."""
        defaults = cls()
        extensions = data.get("audio_extensions", defaults.audio_extensions)
        if not isinstance(extensions, list):
            extensions = defaults.audio_extensions
        return cls(
            model=str(data.get("model", defaults.model)),
            inference_endpoint=str(
                data.get("inference_endpoint", defaults.inference_endpoint),
            ),
            request_timeout_seconds=int(
                data.get("request_timeout_seconds", defaults.request_timeout_seconds),
            ),
            streaming_enabled=bool(
                data.get("streaming_enabled", defaults.streaming_enabled),
            ),
            output_dir=str(data.get("output_dir", defaults.output_dir)),
            recursive=bool(data.get("recursive", defaults.recursive)),
            audio_extensions=[str(extension) for extension in extensions],
        )


@dataclass(slots=True)
class ProcessingConfig:
    """Configuration for merged transcription processing."""

    enabled: bool = True
    output_filename: str = "merged_transcription.json"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create processing configuration from a raw mapping."""
        defaults = cls()
        return cls(
            enabled=bool(data.get("enabled", defaults.enabled)),
            output_filename=str(data.get("output_filename", defaults.output_filename)),
        )


@dataclass(slots=True)
class AnalysisLLMConfig:
    """Configuration for future LLM-assisted analysis calls."""

    backend: str = "api"
    model: str = "Auto"
    cursor_command: str = "agent"
    cursor_args: list[str] = field(default_factory=lambda: ["-p"])
    timeout_seconds: int = 900
    retries: int = 2
    cursor_cli_probe: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create LLM configuration from a raw mapping."""
        defaults = cls()
        cursor_args = data.get("cursor_args", defaults.cursor_args)
        if not isinstance(cursor_args, list):
            cursor_args = defaults.cursor_args
        return cls(
            backend=str(data.get("backend", defaults.backend)),
            model=str(data.get("model", defaults.model)),
            cursor_command=str(data.get("cursor_command", defaults.cursor_command)),
            cursor_args=[str(arg) for arg in cursor_args],
            timeout_seconds=int(data.get("timeout_seconds", defaults.timeout_seconds)),
            retries=int(data.get("retries", defaults.retries)),
            cursor_cli_probe=bool(
                data.get("cursor_cli_probe", defaults.cursor_cli_probe),
            ),
        )


@dataclass(slots=True)
class AnalysisConfig:
    """Configuration for blackboard analysis output and loop behavior."""

    enabled: bool = True
    pipeline: str = "blackboard_v1"
    output_dir: str = "analysis"
    summary_markdown_filename: str = "session_summary.md"
    summary_json_filename: str = "session_summary.json"
    max_audit_attempts: int = 3
    target_window_seconds: float = 90.0
    overlap_seconds: float = 20.0
    llm: AnalysisLLMConfig = field(default_factory=AnalysisLLMConfig)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create analysis configuration from a raw mapping."""
        defaults = cls()
        llm_data = data.get("llm", {})
        if not isinstance(llm_data, Mapping):
            llm_data = {}
        return cls(
            enabled=bool(data.get("enabled", defaults.enabled)),
            pipeline=str(data.get("pipeline", defaults.pipeline)),
            output_dir=str(data.get("output_dir", defaults.output_dir)),
            summary_markdown_filename=str(
                data.get(
                    "summary_markdown_filename",
                    defaults.summary_markdown_filename,
                ),
            ),
            summary_json_filename=str(
                data.get("summary_json_filename", defaults.summary_json_filename),
            ),
            max_audit_attempts=int(
                data.get("max_audit_attempts", defaults.max_audit_attempts),
            ),
            target_window_seconds=float(
                data.get("target_window_seconds", defaults.target_window_seconds),
            ),
            overlap_seconds=float(
                data.get("overlap_seconds", defaults.overlap_seconds),
            ),
            llm=AnalysisLLMConfig.from_mapping(llm_data),
        )


@dataclass(slots=True)
class TaraConfig:
    """Root configuration object for the standalone Tara application."""

    language: str = "fr"
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Self:
        """Create a root configuration object from a raw mapping."""
        return cls(
            language=str(data.get("language", "fr")),
            logging=LoggingConfig.from_mapping(_mapping(data.get("logging"))),
            transcription=TranscriptionConfig.from_mapping(
                _mapping(data.get("transcription")),
            ),
            processing=ProcessingConfig.from_mapping(_mapping(data.get("processing"))),
            analysis=AnalysisConfig.from_mapping(_mapping(data.get("analysis"))),
        )


def load_dotenv(path: Path | None = None) -> None:
    """Load simple `KEY=VALUE` entries from a `.env` file into the environment.

    Existing environment variables are preserved.
    """
    dotenv_path = path or _project_root() / ".env"
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def find_default_config_path() -> Path | None:
    """Return the bundled default configuration path when it exists."""
    config_path = _project_root() / "config" / "configuration.json"
    return config_path if config_path.exists() else None


def load_config(config_path: Path | None = None) -> TaraConfig:
    """Load Tara configuration from JSON, falling back to defaults.

    Args:
        config_path: Optional explicit configuration path.

    Returns:
        Parsed configuration object.
    """
    load_dotenv()
    path = config_path or find_default_config_path()
    if path is None:
        return TaraConfig()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("Configuration root must be a JSON object.")
    return TaraConfig.from_mapping(raw)


def _mapping(value: Any) -> Mapping[str, Any]:
    """Return a mapping value or an empty mapping for invalid sections."""
    return value if isinstance(value, Mapping) else {}


def _project_root() -> Path:
    """Return the source-tree project root for bundled config and `.env`."""
    return Path(__file__).resolve().parents[2]
