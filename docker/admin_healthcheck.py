#!/usr/bin/env python3
"""Bounded healthcheck for the loopback operator dashboard."""

from __future__ import annotations

import sys
from urllib.error import URLError
from urllib.request import urlopen


def main() -> int:
    try:
        with urlopen("http://127.0.0.1:8765/health", timeout=2) as response:
            return 0 if response.status == 200 and response.read(16) == b"ok" else 1
    except (OSError, URLError, TimeoutError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
