#!/usr/bin/env python3
"""Bounded container healthcheck without shell interpolation."""

from __future__ import annotations

import sys
from urllib.error import URLError
from urllib.request import urlopen


def main() -> int:
    try:
        for endpoint in ("live", "ready"):
            with urlopen(
                f"http://127.0.0.1:8000/api/v1/{endpoint}", timeout=2
            ) as response:
                if response.status != 200 or response.read(1024) == b"":
                    return 1
    except (OSError, URLError, TimeoutError):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
