"""Anonymous, coarse page-view counters for the local operator dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request, Response

from tara_web.db.repositories.analytics import AnalyticsRepository

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.post("/page-view", status_code=204, response_class=Response)
def record_page_view(
    request: Request,
    page: Literal["new_job", "help", "upload_session", "job"],
) -> Response:
    AnalyticsRepository(request.app.state.database).record_page_view(
        page=page,
        day=datetime.now(UTC).date().isoformat(),
    )
    return Response(status_code=204)
