"""Unauthenticated technical health endpoints."""

import os
import sqlite3

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["technical"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: str


@router.get("/live", response_model=HealthResponse)
def live() -> HealthResponse:
    return HealthResponse(status="live")


@router.get("/ready", response_model=HealthResponse)
def ready(request: Request) -> HealthResponse:
    from fastapi import HTTPException

    app = request.app
    if not getattr(app.state, "ready", False) or getattr(app.state, "draining", False):
        raise HTTPException(status_code=503, detail="service is not ready")
    try:
        app.state.sqlite.execute("SELECT 1").fetchone()
        storage = app.state.runtime_config.web.storage
        for path in (
            storage.root,
            storage.backups_root,
            storage.sqlite_path.parent,
        ):
            if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
                raise OSError("storage path is unavailable")
    except (AttributeError, OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="service is not ready") from exc
    return HealthResponse(status="ready")
