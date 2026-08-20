# ruff: noqa: E501
"""Native development entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=os.environ.get("TARA_WEB_CONFIG")
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    if arguments.config is None:
        parser.error("--config or TARA_WEB_CONFIG is required")
    frontend_dist = os.environ.get("TARA_WEB_FRONTEND_DIST")
    app = create_app(
        load_config(arguments.config),
        Path(frontend_dist) if frontend_dist else None,
    )
    # The application emits a normalized access log that never contains opaque IDs.
    uvicorn.run(app, host=arguments.host, port=arguments.port, access_log=False)


if __name__ == "__main__":
    main()
