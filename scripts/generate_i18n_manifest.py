"""Generate a validated backend manifest from the frontend i18n catalogues."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
DEFAULT_SOURCE = ROOT / "webinterface/frontend/src/i18n"
DEFAULT_OUTPUT = ROOT / "src/tara_web/i18n_manifest.json"


def render(source: Path = DEFAULT_SOURCE) -> str:
    try:
        required = json.loads(
            (source / "i18n_required_keys.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("required i18n keys are invalid") from exc
    if (
        not isinstance(required, list)
        or not required
        or not all(isinstance(key, str) and key for key in required)
        or len(required) != len(set(required))
    ):
        raise ValueError("required i18n keys are invalid")
    languages: dict[str, list[str]] = {}
    for path in sorted(source.glob("*.json")):
        if path.name == "i18n_required_keys.json":
            continue
        if not LANGUAGE_TAG.fullmatch(path.stem):
            raise ValueError("i18n language filename is invalid")
        try:
            catalogue = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("i18n catalogue is invalid") from exc
        if not isinstance(catalogue, dict) or not all(
            isinstance(key, str)
            and key
            and isinstance(value, str)
            and value
            for key, value in catalogue.items()
        ):
            raise ValueError("i18n catalogue is invalid")
        languages[path.stem] = sorted(catalogue)
    if "fr" not in languages:
        raise ValueError("default French catalogue is missing")
    return json.dumps(
        {"required_keys": required, "languages": languages},
        indent=2,
        sort_keys=True,
    ) + "\n"


def generate(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT) -> None:
    output.write_text(render(source), encoding="utf-8")


def check(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT) -> None:
    if not output.is_file() or output.read_text(encoding="utf-8") != render(source):
        raise ValueError("i18n manifest is stale")


if __name__ == "__main__":
    try:
        check() if "--check" in sys.argv else generate()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
