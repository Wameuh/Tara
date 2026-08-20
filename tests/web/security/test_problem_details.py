# ruff: noqa: ANN001
from __future__ import annotations

from fastapi.testclient import TestClient

from tara_web.app import create_app
from tara_web.config import RuntimeConfig, StorageConfig, WebinterfaceConfig


def test_api_404_and_origin_are_problem_details(tmp_path) -> None:
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
    with TestClient(app) as client:
        for response in (
            client.get("/api/v1/missing"),
            client.post("/api/v1/live"),
            client.post(
                "/api/v1/uploads/sessions", headers={"Origin": "https://evil.invalid"}
            ),
        ):
            assert response.headers["content-type"].startswith(
                "application/problem+json"
            )
            assert response.headers["cache-control"] == "no-store"
            assert "correlation_id" in response.json()
            assert response.json()["code"] in {
                "resource_unavailable",
                "method_not_allowed",
                "forbidden",
            }


def test_api_rate_limit_and_internal_error_are_problem_details(tmp_path) -> None:
    config = RuntimeConfig(
        web=WebinterfaceConfig(
            storage=StorageConfig(
                root=tmp_path / "r",
                backups_root=tmp_path / "b",
                sqlite_path=tmp_path / "r" / "d.sqlite",
            ),
            limits={"rate_limit_polling": 1, "rate_limit_global": 10},
        )
    )
    app = create_app(config)

    @app.post("/api/v1/test-crash")
    def crash() -> None:
        raise RuntimeError("sensitive failure")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/live").status_code == 200
        limited = client.get("/api/v1/live")
        assert limited.status_code == 429
        assert limited.json()["code"] == "rate_limited"
        assert "retry-after" in limited.headers
        failed = client.post("/api/v1/test-crash")
        assert failed.status_code == 500
        assert failed.json()["code"] == "internal_error"
        assert "sensitive" not in failed.text
