from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tara_web.app import create_app
from tara_web.config import (
    LimitsConfig,
    RuntimeConfig,
    StorageConfig,
    WebinterfaceConfig,
)


def test_pending_upload_ttl_cannot_be_shorter_than_frontend_hashing_budget() -> None:
    with pytest.raises(ValidationError):
        LimitsConfig(upload_inactivity_seconds=1799)


def test_openapi_documents_problem_details_and_sse(tmp_path: Path) -> None:
    app = create_app(
        RuntimeConfig(
            web=WebinterfaceConfig(
                storage=StorageConfig(
                    root=tmp_path / "r",
                    backups_root=tmp_path / "b",
                    sqlite_path=tmp_path / "r" / "d.sqlite",
                )
            )
        )
    )
    schema = app.openapi()
    assert "ProblemDetails" in schema["components"]["schemas"]
    assert "PublicEventEnvelope" in schema["components"]["schemas"]
    operation = schema["paths"]["/api/v1/jobs/{job_id}/events"]["get"]
    assert "text/event-stream" in operation["responses"]["200"]["content"]
    for status in ("400", "403", "404", "405", "409", "422", "428", "429", "500"):
        assert "application/problem+json" in operation["responses"][status]["content"]
    create_upload = schema["paths"]["/api/v1/uploads/sessions"]["post"]
    required_headers = {
        parameter["name"]
        for parameter in create_upload["parameters"]
        if parameter["in"] == "header" and parameter["required"]
    }
    assert required_headers == {"idempotency-key", "x-tara-creation-recovery"}
