# ruff: noqa: E402, E501
"""Generate the deterministic OpenAPI document consumed by openapi-typescript."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from tara_web.app import create_app
from tara_web.config import RuntimeConfig, StorageConfig, WebinterfaceConfig

OUTPUT = REPOSITORY_ROOT / "webinterface/frontend/src/api/openapi.json"


def render() -> str:
    config = RuntimeConfig(
        web=WebinterfaceConfig(
            storage=StorageConfig(
                root=Path("/tmp/tara-web"),
                backups_root=Path("/tmp/tara-backups"),
                sqlite_path=Path("/tmp/tara-web/tara.sqlite3"),
            )
        )
    )
    import json

    return json.dumps(create_app(config).openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    expected = render()
    if arguments.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit(
                "generated TypeScript API types are stale; run generate_web_openapi.py"
            )
        return
    OUTPUT.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
