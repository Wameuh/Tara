"""Privacy-preserving structured access log fields."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

# Reuse Uvicorn's configured error handler while keeping raw access logs disabled.
LOGGER = logging.getLogger("uvicorn.error.tara_access")


def normalized_route(request: Request) -> str:
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None)
    if path_format:
        return str(path_format)[:256]
    if request.url.path.startswith("/api/"):
        return "/api/{unmatched}"
    return "/{unmatched}"


async def log_access(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        LOGGER.info(
            "access route=%s status=500 duration_ms=%s correlation_id=%s",
            normalized_route(request),
            round((time.monotonic() - started) * 1000),
            getattr(request.state, "correlation_id", "unknown"),
        )
        raise
    LOGGER.info(
        "access route=%s status=%s duration_ms=%s correlation_id=%s",
        normalized_route(request),
        response.status_code,
        round((time.monotonic() - started) * 1000),
        getattr(request.state, "correlation_id", "unknown"),
    )
    return response
