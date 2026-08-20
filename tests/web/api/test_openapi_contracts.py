from __future__ import annotations

from pathlib import Path

from tara_web.app import create_app
from tara_web.config import RuntimeConfig, StorageConfig, WebinterfaceConfig


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
