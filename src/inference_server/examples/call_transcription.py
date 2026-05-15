"""Example client calling the OpenAI-compatible transcription endpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Call local transcription endpoint.")
    parser.add_argument("audio", type=Path, help="Path to audio file.")
    parser.add_argument("--url", default="http://localhost:8000/v1/audio/transcriptions", help="Endpoint URL.")
    parser.add_argument("--model", default="large-v3", help="Model name.")
    parser.add_argument("--language", default=None, help="Language code (optional).")
    parser.add_argument("--token", default=None, help="Bearer token if required.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    headers: dict[str, str] = {}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    with args.audio.open("rb") as handle:
        files = {"file": (args.audio.name, handle, "application/octet-stream")}
        data: dict[str, str] = {"model": args.model}
        if args.language:
            data["language"] = args.language
        response = requests.post(args.url, headers=headers, files=files, data=data, timeout=120)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

