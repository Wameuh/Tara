"""Bounded safe YAML helpers used for untrusted Tara artifacts."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.events import (
    AliasEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)


class YamlSecurityError(ValueError, YAMLError):
    """A YAML document exceeded a safety limit or used a forbidden construct."""


@dataclass(frozen=True)
class YamlLimits:
    """Resource limits for a single untrusted YAML document."""

    max_bytes: int = 8 * 1024 * 1024
    max_depth: int = 64
    max_nodes: int = 250_000
    max_scalar_chars: int = 2_000_000
    max_aliases: int = 0
    max_seconds: float = 5.0


DEFAULT_YAML_LIMITS = YamlLimits()
MERGED_TRANSCRIPTION_LIMITS = YamlLimits(
    max_bytes=32 * 1024 * 1024,
    max_nodes=1_000_000,
    max_scalar_chars=32 * 1024 * 1024,
    max_seconds=10.0,
)
PUBLIC_RESULT_LIMITS = YamlLimits(
    max_bytes=4 * 1024 * 1024,
    max_nodes=2_000_000,
    max_scalar_chars=100_000,
    max_seconds=3.0,
)
_FENCE_PATTERN = re.compile(r"```(?:yaml|yml|json)?\s*\n(.*?)```", re.DOTALL | re.I)
_AMBIGUOUS_PLAIN_SCALARS = frozenset({"yes", "no", "on", "off"})


def _configure_yaml(yaml: YAML) -> YAML:
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    yaml.allow_unicode = True
    return yaml


def _safe_yaml() -> YAML:
    """Return a safe loader/dumper; never use round-trip mode for artifacts."""
    yaml = _configure_yaml(YAML(typ="safe", pure=True))
    yaml.allow_duplicate_keys = False
    return yaml


def to_yaml(obj: Any, *, preserve_comments: bool = False) -> str:
    """Serialize JSON-compatible data to YAML.

    ``preserve_comments`` is retained for API compatibility; untrusted artifact
    serialization deliberately always uses the safe emitter.
    """
    stream = StringIO()
    yaml = _configure_yaml(YAML()) if preserve_comments else _safe_yaml()
    yaml.dump(obj, stream)
    return stream.getvalue()


def _check_yaml_events(text: str, limits: YamlLimits) -> None:
    started = time.monotonic()
    depth = nodes = aliases = documents = 0
    try:
        events = _safe_yaml().parse(text)
        for event in events:
            if time.monotonic() - started > limits.max_seconds:
                raise YamlSecurityError("YAML parsing timed out")
            if isinstance(event, DocumentStartEvent):
                documents += 1
                if documents > 1:
                    raise YamlSecurityError("YAML multi-document streams are forbidden")
            if isinstance(
                event, (ScalarEvent, MappingStartEvent, SequenceStartEvent, AliasEvent)
            ):
                nodes += 1
                if nodes > limits.max_nodes:
                    raise YamlSecurityError("YAML node limit exceeded")
            if isinstance(event, AliasEvent):
                aliases += 1
                if aliases > limits.max_aliases:
                    raise YamlSecurityError("YAML alias limit exceeded")
            if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                depth += 1
                if depth > limits.max_depth:
                    raise YamlSecurityError("YAML nesting limit exceeded")
            elif isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                depth -= 1
            if isinstance(event, ScalarEvent):
                if len(event.value) > limits.max_scalar_chars:
                    raise YamlSecurityError("YAML scalar limit exceeded")
                if (
                    event.style is None
                    and event.value.casefold() in _AMBIGUOUS_PLAIN_SCALARS
                ):
                    raise YamlSecurityError("ambiguous implicit YAML scalar")
            tag = getattr(event, "tag", None)
            if tag is not None:
                raise YamlSecurityError("explicit YAML tags are forbidden")
        if documents != 1:
            raise YamlSecurityError("YAML must contain exactly one document")
    except YamlSecurityError:
        raise
    except YAMLError as exc:
        raise YamlSecurityError("invalid YAML") from exc


def from_yaml(text: str, *, limits: YamlLimits = DEFAULT_YAML_LIMITS) -> Any:
    """Safely parse one bounded YAML document into JSON-compatible values."""
    if not isinstance(text, str):
        raise TypeError("YAML text must be a string")
    if len(text.encode("utf-8")) > limits.max_bytes:
        raise YamlSecurityError("YAML byte limit exceeded")
    _check_yaml_events(text, limits)
    try:
        value = _safe_yaml().load(text)
    except YAMLError as exc:
        raise YamlSecurityError("invalid YAML") from exc
    validate_json_like(value, limits)
    return value


def validate_json_like(value: Any, limits: YamlLimits) -> None:
    """Validate an already parsed tree without recursive alias expansion."""
    deadline = time.monotonic() + limits.max_seconds
    seen: set[int] = set()
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        if time.monotonic() > deadline:
            raise YamlSecurityError("YAML parsing timed out")
        nodes += 1
        if nodes > limits.max_nodes:
            raise YamlSecurityError("YAML node limit exceeded")
        if depth > limits.max_depth:
            raise YamlSecurityError("YAML nesting limit exceeded")
        if item is None or isinstance(item, str | int | float | bool):
            if isinstance(item, float) and not math.isfinite(item):
                raise YamlSecurityError("non-finite numeric values are forbidden")
            if isinstance(item, str) and len(item) > limits.max_scalar_chars:
                raise YamlSecurityError("YAML scalar limit exceeded")
            continue
        item_id = id(item)
        if item_id in seen:
            raise YamlSecurityError("YAML aliases and cycles are forbidden")
        seen.add(item_id)
        if isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
            continue
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for key, child in item.items():
                if len(key) > limits.max_scalar_chars:
                    raise YamlSecurityError("YAML scalar limit exceeded")
                nodes += 1
                if nodes > limits.max_nodes:
                    raise YamlSecurityError("YAML node limit exceeded")
                stack.append((child, depth + 1))
            continue
        raise YamlSecurityError("YAML contains a non-JSON value")


def write_yaml(path: Path, obj: Any, *, preserve_comments: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_yaml(obj, preserve_comments=preserve_comments), encoding="utf-8")


def read_yaml(path: Path, *, limits: YamlLimits = DEFAULT_YAML_LIMITS) -> Any:
    data = _read_bounded(path, limits.max_bytes)
    if len(data) > limits.max_bytes:
        raise YamlSecurityError("YAML byte limit exceeded")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise YamlSecurityError("invalid UTF-8 YAML") from exc
    return from_yaml(text, limits=limits)


def read_json(path: Path, *, limits: YamlLimits = DEFAULT_YAML_LIMITS) -> Any:
    """Read one bounded UTF-8 JSON document without loading an entire file."""
    data = _read_bounded(path, limits.max_bytes)
    if len(data) > limits.max_bytes:
        raise YamlSecurityError("JSON byte limit exceeded")
    try:
        return from_json(data.decode("utf-8"), limits=limits)
    except UnicodeDecodeError as exc:
        raise YamlSecurityError("invalid UTF-8 JSON") from exc


def json_text_to_yaml_text(
    json_text: str, *, limits: YamlLimits = DEFAULT_YAML_LIMITS
) -> str:
    return to_yaml(from_json(json_text, limits=limits))


def json_obj_to_yaml(obj: Any) -> str:
    return to_yaml(obj)


def load_yaml_or_json(path: Path) -> Any:
    suffix = path.suffix.lower()
    if suffix == ".json":
        value = read_json(path)
    elif suffix in {".yaml", ".yml"}:
        value = read_yaml(path)
    else:
        raise ValueError(f"Unsupported configuration format: {path.suffix}")
    return value


def _read_bounded(path: Path, max_bytes: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(max_bytes + 1)


def from_json(text: str, *, limits: YamlLimits = DEFAULT_YAML_LIMITS) -> Any:
    """Parse bounded legacy JSON with the same structural limits as YAML."""
    if not isinstance(text, str):
        raise TypeError("JSON text must be a string")
    if len(text.encode("utf-8")) > limits.max_bytes:
        raise YamlSecurityError("JSON byte limit exceeded")
    started = time.monotonic()
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise YamlSecurityError("invalid JSON") from exc
    remaining = max(0.001, limits.max_seconds - (time.monotonic() - started))
    validate_json_like(
        value,
        YamlLimits(
            max_bytes=limits.max_bytes,
            max_depth=limits.max_depth,
            max_nodes=limits.max_nodes,
            max_scalar_chars=limits.max_scalar_chars,
            max_aliases=limits.max_aliases,
            max_seconds=remaining,
        ),
    )
    return value


def iter_yaml_docs(path: Path) -> Iterator[Any]:
    """Read trusted internal multi-document streams.

    Versioned artifact loaders use :func:`read_yaml` and therefore reject them.
    """
    yaml = _safe_yaml()
    with path.open(encoding="utf-8") as handle:
        yield from (item for item in yaml.load_all(handle) if item is not None)


def write_yaml_docs(path: Path, docs: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        _safe_yaml().dump_all(list(docs), handle)


def repair_yaml_blob(blob: str) -> str:
    text = blob.strip()
    match = _FENCE_PATTERN.search(text)
    text = match.group(1).strip() if match is not None else text
    lines = text.splitlines()
    if lines and lines[-1].strip().endswith(":"):
        lines.pop()
    for _ in range(min(3, len(lines))):
        try:
            from_yaml("\n".join(lines))
            return "\n".join(lines)
        except YAMLError:
            lines.pop()
    return "\n".join(lines)


def parse_yaml_with_repair(blob: str) -> Any:
    return from_yaml(repair_yaml_blob(blob))
