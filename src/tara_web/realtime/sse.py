"""Small SSE formatter used only after header based authorization."""

from __future__ import annotations

import json


def event(payload: dict[str, object]) -> bytes:
    return (
        b"event: tara\ndata: "
        + json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
        + b"\n\n"
    )


def heartbeat() -> bytes:
    return b": heartbeat\n\n"
