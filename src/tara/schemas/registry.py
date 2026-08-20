"""Closed schema loader registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tara.yaml_utils import (
    MERGED_TRANSCRIPTION_LIMITS,
    PUBLIC_RESULT_LIMITS,
    YamlLimits,
    from_yaml,
    read_json,
    read_yaml,
)

from .merged_transcription import SCHEMA_NAME as MERGED_NAME
from .merged_transcription import MergedTranscription
from .migrations import ADAPTERS
from .public_result import SCHEMA_NAME as RESULT_NAME
from .public_result import PublicResult


def load_merged_transcription_text(
    text: str, *, limits: YamlLimits | None = None
) -> MergedTranscription:
    return _load(
        from_yaml(text, limits=limits or MERGED_TRANSCRIPTION_LIMITS),
        MERGED_NAME,
        MergedTranscription,
    )


def load_public_result_text(
    text: str, *, limits: YamlLimits | None = None
) -> PublicResult:
    return _load(
        from_yaml(text, limits=limits or PUBLIC_RESULT_LIMITS),
        RESULT_NAME,
        PublicResult,
    )


def load_merged_transcription(
    path: Path, *, limits: YamlLimits | None = None
) -> MergedTranscription:
    selected_limits = limits or MERGED_TRANSCRIPTION_LIMITS
    if path.suffix.lower() == ".json":
        value = read_json(path, limits=selected_limits)
        if not isinstance(value, dict):
            raise ValueError("legacy JSON root must be a mapping")
        if "schema_name" in value or "schema_version" in value:
            raise ValueError("current merged-transcription JSON is unsupported")
        return _load(
            value,
            MERGED_NAME,
            MergedTranscription,
        )
    return _load(
        read_yaml(path, limits=selected_limits),
        MERGED_NAME,
        MergedTranscription,
    )


def load_public_result(path: Path) -> PublicResult:
    return _load(
        read_yaml(path, limits=PUBLIC_RESULT_LIMITS), RESULT_NAME, PublicResult
    )


def _load(
    value: Any,
    name: str,
    model: type[MergedTranscription] | type[PublicResult],
) -> MergedTranscription | PublicResult:
    if not isinstance(value, dict):
        raise ValueError("schema root must be a mapping")
    if value.get("schema_name") == name and value.get("schema_version") == "26.0.1":
        return model.model_validate(value)
    adapter_key = _adapter_key(value, name)
    adapter = ADAPTERS.get(adapter_key)
    if adapter is not None:
        return adapter(value)
    raise ValueError("schema name or version is unsupported")


def _adapter_key(value: dict[str, Any], name: str) -> tuple[str, str | int]:
    if (
        name == MERGED_NAME
        and "schema_name" not in value
        and "schema_version" not in value
    ):
        return name, "legacy-v0"
    if name == RESULT_NAME and "schema_name" not in value:
        return name, value.get("schema_version")
    return name, "unsupported"
