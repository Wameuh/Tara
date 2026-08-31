#!/usr/bin/env python3
"""Fail closed before making the container reachable."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

from tara_web.catalogs import validate_catalogues
from tara_web.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    arguments = parser.parse_args()
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SystemExit("tara-web must not run as root")
    runtime = load_config(arguments.config)
    _validate_inference_credentials(runtime.tara_config_snapshot)
    _validate_cursor_backend(runtime.tara_config_snapshot)
    storage = runtime.web.storage
    for path in (storage.root, storage.sqlite_path.parent, storage.backups_root):
        _private_writable_directory(path)
    if shutil.disk_usage(storage.root).free < runtime.web.limits.min_free_storage_bytes:
        raise SystemExit("insufficient free storage")
    if sqlite3.sqlite_version_info < (3, 35, 0):
        raise SystemExit("SQLite 3.35 or newer is required")
    completed = subprocess.run(
        ["ffmpeg", "-version"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit("FFmpeg preflight failed")
    validate_catalogues(
        Path(__file__).parents[1] / "src/tara_web/i18n_manifest.json",
        runtime.web.supported_languages,
        runtime.web.default_language,
    )


def _validate_inference_credentials(
    tara_config_snapshot: str | None,
    environ: dict[str, str] | None = None,
) -> None:
    """Fail before startup when the selected inference credential type is wrong."""
    if tara_config_snapshot is None:
        return
    try:
        document = json.loads(tara_config_snapshot)
        transcription = document["transcription"]
        provider = str(transcription["inference_auth_provider"]).strip().lower()
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("Tara inference configuration is invalid") from exc

    environment = environ if environ is not None else os.environ
    if provider in {"", "none"}:
        return
    if provider == "modal_proxy":
        key_name = str(
            transcription.get("modal_proxy_key_env", "TARA_MODAL_PROXY_AUTH_KEY")
        )
        secret_name = str(
            transcription.get("modal_proxy_secret_env", "TARA_MODAL_PROXY_AUTH_SECRET")
        )
        if not (
            _valid_provider_token(environment.get(key_name), "wk-")
            and _valid_provider_token(environment.get(secret_name), "ws-")
        ):
            raise SystemExit("Modal proxy credentials are missing or malformed")
        return
    if provider == "modal_map":
        if not (
            _valid_provider_token(environment.get("MODAL_TOKEN_ID"), "ak-")
            and _valid_provider_token(environment.get("MODAL_TOKEN_SECRET"), "as-")
        ):
            raise SystemExit("Modal API credentials are missing or malformed")
        return
    raise SystemExit("Tara inference provider is unsupported")


def _valid_provider_token(value: str | None, prefix: str) -> bool:
    if value is None or value != value.strip() or not 16 <= len(value) <= 512:
        return False
    return value.startswith(prefix) and all(
        character.isascii() and (character.isalnum() or character in {"-", "_"})
        for character in value
    )


def _validate_cursor_backend(
    tara_config_snapshot: str | None,
    environ: dict[str, str] | None = None,
    command_finder=shutil.which,
) -> None:
    """Fail startup when a selected Cursor CLI runtime is incomplete."""
    if tara_config_snapshot is None:
        return
    try:
        document = json.loads(tara_config_snapshot)
        llm = document["analysis"]["llm"]
        backend = str(llm["backend"]).strip().lower()
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit("Tara analysis configuration is invalid") from exc
    if backend != "cursor_cli":
        return

    cursor_command = str(llm.get("cursor_command", "cursor-agent")).strip()
    if not cursor_command or command_finder(cursor_command) is None:
        raise SystemExit("Cursor CLI executable is unavailable")
    if command_finder("bwrap") is None:
        raise SystemExit("Cursor CLI sandbox dependency is unavailable")

    environment = environ if environ is not None else os.environ
    config_home = environment.get("XDG_CONFIG_HOME")
    if config_home:
        auth_path = Path(config_home) / "cursor" / "auth.json"
    else:
        auth_path = Path(environment.get("HOME", "")) / ".config/cursor/auth.json"
    try:
        info = os.lstat(auth_path)
    except OSError as exc:
        raise SystemExit("Cursor CLI authentication is unavailable") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_size <= 0
        or (os.name != "nt" and info.st_mode & 0o077)
    ):
        raise SystemExit("Cursor CLI authentication is invalid")


def _private_writable_directory(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise SystemExit("required volume is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SystemExit("required volume is invalid")
    if os.name != "nt" and info.st_mode & 0o077:
        raise SystemExit("required volume permissions are unsafe")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise SystemExit("required volume is not writable")


if __name__ == "__main__":
    main()
