"""End-to-end audio job through HTTP upload, spawn worker and real Tara engine."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tara.schemas.registry import load_merged_transcription
from tara_web.app import create_app
from tara_web.config import load_config


class _InferenceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(size)
        payload = json.dumps(
            {
                "text": "La porte ancienne est ouverte.",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 0.2,
                        "text": "La porte ancienne est ouverte.",
                    }
                ],
                "language": "fr",
                "duration": 0.2,
                "model": "integration-test",
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *args: object) -> None:
        return


def _audio(path: Path, codec: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            codec,
            str(path),
        ],
        check=False,
        capture_output=True,
        timeout=20,
    )
    if completed.returncode:
        pytest.skip(f"ffmpeg codec {codec} is unavailable")
    return path.read_bytes()


def _configuration(tmp_path: Path, endpoint: str) -> object:
    tara = tmp_path / "tara.yaml"
    tara.write_text(
        f"""transcription:
  inference_endpoint: {endpoint}
  inference_auth_provider: none
  request_timeout_seconds: 10
  streaming_enabled: false
  parallelism: 2
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
    validation_concurrency: 2
    ffprobe_timeout_seconds: 10
    ffmpeg_timeout_seconds: 10
""",
        encoding="utf-8",
    )
    return load_config(web)


def _upload_track(
    client: TestClient,
    session_id: str,
    secret: str,
    filename: str,
    mime: str,
    content: bytes,
) -> str:
    digest = hashlib.sha256(content).hexdigest()
    declared = client.post(
        f"/api/v1/uploads/sessions/{session_id}/files",
        headers={
            "X-Tara-Job-Secret": secret,
            "Idempotency-Key": f"declare-{filename}",
        },
        json={
            "filename": filename,
            "size": len(content),
            "sha256": digest,
            "mime": mime,
        },
    )
    assert declared.status_code == 201, declared.text
    file_id = declared.json()["file_id"]
    uploaded = client.patch(
        f"/api/v1/uploads/sessions/{session_id}/files/{file_id}/chunks",
        headers={
            "X-Tara-Job-Secret": secret,
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
            "X-Tara-Job-Secret": secret,
            "Expected-Revision": str(uploaded.json()["revision"]),
            "Idempotency-Key": f"finalize-{filename}",
        },
    )
    assert finalized.status_code == 202, finalized.text
    return file_id


def _wait_for(
    client: TestClient,
    path: str,
    headers: dict[str, str],
    terminal: set[str],
    *,
    timeout: float = 30,
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


def test_real_supported_audio_job_runs_through_spawn_and_publishes_public_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mp3 = _audio(tmp_path / "Alice.mp3", "libmp3lame")
    ogg = _audio(tmp_path / "MaitreDuJeu.ogg", "libvorbis")
    aac = _audio(tmp_path / "Guest.aac", "aac")
    m4a = _audio(tmp_path / "Music.m4a", "aac")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _InferenceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("TARA_INFERENCE_ENDPOINT", endpoint)
    monkeypatch.setenv("TARA_INFERENCE_AUTH_PROVIDER", "none")
    monkeypatch.setenv("TARA_TRANSCRIPTION_PARALLELISM", "2")
    monkeypatch.setenv("TARA_ANALYSIS_PARALLEL", "false")
    try:
        with TestClient(create_app(_configuration(tmp_path, endpoint))) as client:
            created = client.post(
                "/api/v1/uploads/sessions",
                headers={"Idempotency-Key": "real-audio-session"},
            ).json()
            session_id, secret = created["session_id"], created["secret"]
            headers = {"X-Tara-Job-Secret": secret}
            _upload_track(client, session_id, secret, "Alice.mp3", "audio/mpeg", mp3)
            _upload_track(
                client,
                session_id,
                secret,
                "MaitreDuJeu.ogg",
                "audio/ogg",
                ogg,
            )
            _upload_track(client, session_id, secret, "Guest.aac", "audio/aac", aac)
            _upload_track(client, session_id, secret, "Music.m4a", "audio/mp4", m4a)
            session = _wait_for(
                client,
                f"/api/v1/sessions/{session_id}",
                headers,
                {"ready"},
            )
            launched = client.post(
                f"/api/v1/sessions/{session_id}/jobs",
                headers={
                    **headers,
                    "Expected-Revision": str(session["revision"]),
                    "Idempotency-Key": "launch-real-audio",
                },
            )
            assert launched.status_code == 201, launched.text
            job_id = launched.json()["job_id"]
            job = _wait_for(
                client,
                f"/api/v1/jobs/{job_id}",
                headers,
                {"completed", "failed", "timed_out", "cancelled"},
                timeout=45,
            )
            assert job["status"] == "completed", job
            assert job["progress"]["overall_ratio"] == 1.0

            result = client.get(f"/api/v1/jobs/{job_id}/result", headers=headers)
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "available"
            with client.app.state.database.transaction() as connection:
                metric = connection.execute(
                    "SELECT job_type,input_file_count,audio_duration_ms,duration_ms "
                    "FROM job_metrics WHERE job_public_id=?",
                    (job_id,),
                ).fetchone()
            assert metric is not None
            assert tuple(metric[:2]) == ("audio", 4)
            # Lossy containers may report encoder padding differently between
            # FFmpeg builds, while still representing the same two 200 ms tracks.
            assert 700 <= metric["audio_duration_ms"] <= 1_000
            assert metric["duration_ms"] >= 0
            assert str(tmp_path) not in result.text
            assert "host.docker.internal" not in result.text

            merged_path = (
                client.app.state.storage_layout.root
                / "jobs"
                / job_id
                / "work"
                / "audio"
                / "transcriptions"
                / "merged_transcription.yaml"
            )
            merged = load_merged_transcription(merged_path)
            speakers = {segment.author.speaker for segment in merged.content.segments}
            source_ids = {
                segment.author.source_file for segment in merged.content.segments
            }
            assert speakers == {"Alice", "MaitreDuJeu", "Guest", "Music"}
            assert len(source_ids) == 4
            assert all(source_id.startswith("uf_") for source_id in source_ids)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_zip_job_extracts_tracks_then_runs_the_audio_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mp3 = _audio(tmp_path / "Alice.mp3", "libmp3lame")
    ogg = _audio(tmp_path / "MaitreDuJeu.ogg", "libvorbis")
    aac = _audio(tmp_path / "Guest.aac", "aac")
    m4a = _audio(tmp_path / "Music.m4a", "aac")
    archive_path = tmp_path / "session.zip"
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("joueurs/Alice.mp3", mp3)
        archive.writestr("MaitreDuJeu.ogg", ogg)
        archive.writestr("Guest.aac", aac)
        archive.writestr("Music.m4a", m4a)
        archive.writestr("notes/readme.txt", b"ignored")
    archive_content = archive_path.read_bytes()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _InferenceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("TARA_INFERENCE_ENDPOINT", endpoint)
    monkeypatch.setenv("TARA_INFERENCE_AUTH_PROVIDER", "none")
    monkeypatch.setenv("TARA_TRANSCRIPTION_PARALLELISM", "2")
    monkeypatch.setenv("TARA_ANALYSIS_PARALLEL", "false")
    try:
        with TestClient(create_app(_configuration(tmp_path, endpoint))) as client:
            created_response = client.post(
                "/api/v1/uploads/sessions?input_type=zip",
                headers={"Idempotency-Key": "real-zip-session"},
            )
            assert created_response.status_code == 201, created_response.text
            created = created_response.json()
            session_id, secret = created["session_id"], created["secret"]
            headers = {"X-Tara-Job-Secret": secret}
            _upload_track(
                client,
                session_id,
                secret,
                "session.zip",
                "application/zip",
                archive_content,
            )
            session = _wait_for(
                client,
                f"/api/v1/sessions/{session_id}",
                headers,
                {"ready"},
            )
            assert session["input_type"] == "zip"
            assert session["archive_excluded_count"] == 1
            assert {item["archive_entry_name"] for item in session["files"]} == {
                "joueurs/Alice.mp3",
                "MaitreDuJeu.ogg",
                "Guest.aac",
                "Music.m4a",
            }
            assert not archive_path_for_session(client, session_id).exists()
            launched = client.post(
                f"/api/v1/sessions/{session_id}/jobs",
                headers={
                    **headers,
                    "Expected-Revision": str(session["revision"]),
                    "Idempotency-Key": "launch-real-zip",
                },
            )
            assert launched.status_code == 201, launched.text
            job_id = launched.json()["job_id"]
            job = _wait_for(
                client,
                f"/api/v1/jobs/{job_id}",
                headers,
                {"completed", "failed", "timed_out", "cancelled"},
                timeout=45,
            )
            assert job["status"] == "completed", job
            result = client.get(f"/api/v1/jobs/{job_id}/result", headers=headers)
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "available"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def archive_path_for_session(client: TestClient, session_id: str) -> Path:
    connection = client.app.state.database.connect()
    try:
        row = connection.execute(
            "SELECT storage_path FROM upload_files f JOIN upload_sessions s "
            "ON s.id=f.session_id WHERE s.public_id=? AND f.archive_parent_id IS NULL",
            (session_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return client.app.state.storage_layout.upload_path(str(row["storage_path"]))
