"""End-to-end merged YAML job through HTTP, spawn and the real Tara engine."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tara.schemas.merged_transcription import (
    SegmentAuthor,
    TranscriptionSegment,
    new_merged_transcription,
)
from tara.yaml_utils import to_yaml
from tara_web.app import create_app
from tara_web.config import load_config

CREATION_RECOVERY = "cnJycnJycnJycnJycnJycnJycnJycnJycnJycnJycnI"


def _configuration(tmp_path: Path) -> object:
    tara = tmp_path / "tara.yaml"
    tara.write_text(
        """transcription:
  inference_endpoint: http://127.0.0.1:1
  inference_auth_provider: none
analysis:
  enabled: true
  parallel: false
  llm:
    backend: deterministic
    model: deterministic
""",
        encoding="utf-8",
    )
    web = tmp_path / "web.yaml"
    web.write_text(
        f"""webinterface:
  storage:
    root: ./runtime
    backups_root: ./backups
    sqlite_path: ./runtime/tara.sqlite3
  public_url: http://127.0.0.1:8000
  security:
    allowed_hosts: [127.0.0.1, testserver]
  runner_mode: tara
  tara_config_path: {tara.name}
  limits:
    max_active_jobs: 1
    validation_concurrency: 1
    merged_validation_timeout_seconds: 15
""",
        encoding="utf-8",
    )
    return load_config(web)


def _wait_for(
    client: TestClient,
    path: str,
    headers: dict[str, str],
    terminal: set[str],
    *,
    timeout: float = 45,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(path, headers=headers)
        if response.status_code == 429:
            time.sleep(0.5)
            continue
        assert response.status_code == 200, response.text
        last = response.json()
        if last.get("status") in terminal:
            return last
        time.sleep(0.25)
    pytest.fail(f"resource did not reach a terminal state: {last}")


def test_real_merged_yaml_job_skips_audio_and_publishes_result(
    tmp_path: Path,
) -> None:
    content = to_yaml(
        new_merged_transcription(
            text="La porte ancienne est ouverte.",
            segments=[
                TranscriptionSegment(
                    start=0,
                    end=1,
                    text="La porte ancienne est ouverte.",
                    author=SegmentAuthor(
                        speaker="MaitreDuJeu", source_file="source_merged_000001"
                    ),
                )
            ],
        ).to_dict()
    ).encode()
    digest = hashlib.sha256(content).hexdigest()
    with TestClient(create_app(_configuration(tmp_path))) as client:
        created_response = client.post(
            "/api/v1/uploads/sessions?input_type=merged_transcription",
            headers={
                "Idempotency-Key": "real-merged-session",
                "X-Tara-Creation-Recovery": CREATION_RECOVERY,
            },
        )
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        session_id, secret = created["session_id"], created["secret"]
        headers = {"X-Tara-Job-Secret": secret}
        declared = client.post(
            f"/api/v1/uploads/sessions/{session_id}/files",
            headers={**headers, "Idempotency-Key": "declare-merged"},
            json={
                "filename": "merged_transcription.yaml",
                "size": len(content),
                "sha256": digest,
                "mime": "application/yaml",
            },
        )
        assert declared.status_code == 201, declared.text
        file_id = declared.json()["file_id"]
        uploaded = client.patch(
            f"/api/v1/uploads/sessions/{session_id}/files/{file_id}/chunks",
            headers={
                **headers,
                "Upload-Offset": "0",
                "Upload-Checksum": digest,
                "Content-Type": "application/octet-stream",
            },
            content=content,
        )
        assert uploaded.status_code == 200, uploaded.text
        finalized = client.post(
            f"/api/v1/uploads/sessions/{session_id}/files/{file_id}/finalize",
            headers={
                **headers,
                "Expected-Revision": str(uploaded.json()["revision"]),
                "Idempotency-Key": "finalize-merged",
            },
        )
        assert finalized.status_code == 202, finalized.text
        session = _wait_for(
            client, f"/api/v1/sessions/{session_id}", headers, {"ready"}
        )
        assert session["input_type"] == "merged_transcription"
        assert session["files"][0]["schema_version"] == "26.0.1"
        assert session["files"][0]["token_count"] > 0
        launched = client.post(
            f"/api/v1/sessions/{session_id}/jobs",
            headers={
                **headers,
                "Expected-Revision": str(session["revision"]),
                "Idempotency-Key": "launch-real-merged",
            },
        )
        assert launched.status_code == 201, launched.text
        job_id = launched.json()["job_id"]
        job = _wait_for(
            client,
            f"/api/v1/jobs/{job_id}",
            headers,
            {"completed", "failed", "timed_out", "cancelled"},
        )
        assert job["status"] == "completed", job
        result = client.get(f"/api/v1/jobs/{job_id}/result", headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "available"
        workspace = client.app.state.storage_layout.root / "jobs" / job_id
        assert not (workspace / "work" / "audio").exists()
