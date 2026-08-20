"""Tests for shared YAML utilities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ruamel.yaml.error import YAMLError

from tara.yaml_utils import (
    YamlLimits,
    YamlSecurityError,
    from_yaml,
    iter_yaml_docs,
    json_obj_to_yaml,
    json_text_to_yaml_text,
    load_yaml_or_json,
    parse_yaml_with_repair,
    read_yaml,
    repair_yaml_blob,
    to_yaml,
    write_yaml,
    write_yaml_docs,
)


def test_to_yaml_from_yaml_round_trip() -> None:
    """Serialized YAML should parse back to the same structure."""
    payload = {"facts": [{"id": "f1", "text": "hello"}], "count": 2}
    text = to_yaml(payload)
    assert from_yaml(text) == payload


def test_json_text_to_yaml_text() -> None:
    """JSON text should convert to equivalent YAML."""
    json_text = '{"a": 1, "b": ["x", "y"]}'
    yaml_text = json_text_to_yaml_text(json_text)
    assert from_yaml(yaml_text) == json.loads(json_text)


def test_json_text_to_yaml_text_applies_common_limits() -> None:
    with pytest.raises(YamlSecurityError, match="byte"):
        json_text_to_yaml_text('{"value":"abcdef"}', limits=YamlLimits(max_bytes=8))


def test_json_obj_to_yaml() -> None:
    """Parsed JSON objects should convert to YAML."""
    obj = {"language": "fr", "segments": [{"start": 0.0, "text": "hi"}]}
    assert from_yaml(json_obj_to_yaml(obj)) == obj


def test_load_yaml_or_json_by_extension(tmp_path: Path) -> None:
    """Loader should accept both JSON and YAML extensions."""
    payload = {"mode": "test", "enabled": True}
    json_path = tmp_path / "config.json"
    yaml_path = tmp_path / "config.yaml"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    write_yaml(yaml_path, payload)
    assert load_yaml_or_json(json_path) == payload
    assert load_yaml_or_json(yaml_path) == payload


def test_load_yaml_or_json_rejects_unknown_extension(tmp_path: Path) -> None:
    """Unsupported extensions should raise ValueError."""
    bad_path = tmp_path / "config.txt"
    bad_path.write_text("x: 1", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported configuration format"):
        load_yaml_or_json(bad_path)


def test_multi_document_yaml_stream(tmp_path: Path) -> None:
    """Multi-document YAML files should round-trip all documents."""
    docs = [{"chunk_id": "c1"}, {"chunk_id": "c2"}]
    path = tmp_path / "evidence_chunks.yaml"
    write_yaml_docs(path, docs)
    loaded = list(iter_yaml_docs(path))
    assert loaded == docs


def test_read_yaml_file(tmp_path: Path) -> None:
    """read_yaml should load a YAML file from disk."""
    path = tmp_path / "artifact.yaml"
    write_yaml(path, {"key": "value"})
    assert read_yaml(path) == {"key": "value"}


def test_repair_yaml_blob_strips_fence() -> None:
    """Repair should extract YAML from fenced blocks."""
    blob = "```yaml\nkey: value\nnested:\n  item: 1\n```"
    repaired = repair_yaml_blob(blob)
    assert from_yaml(repaired) == {"key": "value", "nested": {"item": 1}}


def test_repair_yaml_blob_drops_truncated_line() -> None:
    """Repair should drop a trailing partial line from truncated output."""
    blob = "items:\n  - id: one\n  - id: two\n  - id:"
    repaired = repair_yaml_blob(blob)
    assert from_yaml(repaired) == {"items": [{"id": "one"}, {"id": "two"}]}


def test_parse_yaml_with_repair_success() -> None:
    """parse_yaml_with_repair should parse valid YAML directly."""
    assert parse_yaml_with_repair("name: test\n") == {"name": "test"}


def test_parse_yaml_with_repair_raises_on_invalid_blob() -> None:
    """Invalid YAML should still raise after repair attempts."""
    with pytest.raises(YAMLError):
        parse_yaml_with_repair("key: [unclosed\nnested: {broken")
