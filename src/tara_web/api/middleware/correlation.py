"""Request correlation without trusting client supplied identifiers."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request, Response


async def correlate(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request.state.correlation_id = secrets.token_urlsafe(12)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = request.state.correlation_id
    return response
