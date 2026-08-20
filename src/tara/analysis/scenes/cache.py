"""Cache helpers for scene pipeline artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tara.yaml_utils import load_yaml_or_json
from tara.yaml_utils import write_yaml as write_yaml_file


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hash of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_hash(payload: Any) -> str:
    """Return a stable SHA-256 hash for a JSON-compatible value."""
    blob = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def read_yaml(path: Path) -> dict[str, Any] | None:
    """Read a YAML or legacy JSON object, returning None on missing/invalid content."""
    if not path.exists():
        return None
    try:
        data = load_yaml_or_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    """Write a private YAML artifact."""
    write_yaml_file(path, payload)


def read_json(path: Path) -> dict[str, Any] | None:
    """Backward-compatible alias for :func:`read_yaml`."""
    return read_yaml(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Backward-compatible alias for :func:`write_yaml`."""
    write_yaml(path, payload)
