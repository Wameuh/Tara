"""Validation of browser message catalogues at process startup."""

from __future__ import annotations

import json
from pathlib import Path

from .config import LANGUAGE_TAG, ConfigError


def validate_catalogues(
    manifest_path: Path, configured_languages: tuple[str, ...], default: str
) -> tuple[str, ...]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        required_keys = manifest["required_keys"]
        catalogues = manifest["languages"]
        if not isinstance(required_keys, list) or not all(
            isinstance(key, str) for key in required_keys
        ):
            raise TypeError
        if len(required_keys) != len(set(required_keys)) or not isinstance(
            catalogues, dict
        ):
            raise TypeError
        required = set(required_keys)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConfigError("i18n manifest is invalid") from exc
    valid: list[str] = []
    for language in configured_languages:
        keys = catalogues.get(language, [])
        if (
            not LANGUAGE_TAG.fullmatch(language)
            or not isinstance(keys, list)
            or not all(isinstance(key, str) for key in keys)
            or len(keys) != len(set(keys))
            or not required.issubset(keys)
        ):
            if language == default:
                raise ConfigError(
                    f"default language catalogue is missing or incomplete: {language}"
                )
            continue
        valid.append(language)
    if default not in valid:
        raise ConfigError("no complete default language catalogue is available")
    return tuple(valid)
