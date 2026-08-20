#!/usr/bin/env python3
"""Fail closed before making the container reachable."""

from __future__ import annotations

import argparse
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
