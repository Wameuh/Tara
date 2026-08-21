"""Run one real MP3/OGG Tara job through the Docker Compose deployment."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_MAX_INFERENCE_REQUEST_BYTES = 16 * 1024 * 1024
_COMPOSE_FILES = (
    "-f",
    str(ROOT / "compose.yaml"),
    "-f",
    str(ROOT / "compose.override.yaml.example"),
)


class InferenceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests_seen = 0

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= _MAX_INFERENCE_REQUEST_BYTES:
            self.send_error(413)
            return
        self.rfile.read(size)
        type(self).requests_seen += 1
        segment = {
            "type": "segment",
            "start": 0.0,
            "end": 0.2,
            "text": "La porte ancienne est ouverte.",
        }
        final = {
            "type": "final",
            "text": "La porte ancienne est ouverte.",
            "language": "fr",
            "duration": 0.2,
            "model": "docker-smoke",
        }
        payload = (
            f"data: {json.dumps(segment)}\n\ndata: {json.dumps(final)}\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *args: object) -> None:
        return


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers=headers or {},
    )
    context: ssl.SSLContext | None = None
    if urllib.parse.urlsplit(base_url).scheme == "https":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {method} {path}: {detail}") from exc
    return json.loads(payload) if payload else {}


def generate_audio(path: Path, codec: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required on the host for this smoke test")
    subprocess.run(
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
            "-y",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    return path.read_bytes()


def upload(
    base_url: str,
    session_id: str,
    secret: str,
    filename: str,
    mime: str,
    content: bytes,
) -> None:
    digest = hashlib.sha256(content).hexdigest()
    common = {"X-Tara-Job-Secret": secret}
    declared = request_json(
        base_url,
        "POST",
        f"/api/v1/uploads/sessions/{session_id}/files",
        headers={
            **common,
            "Content-Type": "application/json",
            "Idempotency-Key": f"docker-declare-{filename}",
        },
        body=json.dumps(
            {
                "filename": filename,
                "size": len(content),
                "sha256": digest,
                "mime": mime,
            }
        ).encode(),
    )
    file_id = declared["file_id"]
    chunk = request_json(
        base_url,
        "PATCH",
        f"/api/v1/uploads/sessions/{session_id}/files/{file_id}/chunks",
        headers={
            **common,
            "Content-Type": "application/octet-stream",
            "Upload-Offset": "0",
            "Upload-Checksum": digest,
        },
        body=content,
    )
    request_json(
        base_url,
        "POST",
        f"/api/v1/uploads/sessions/{session_id}/files/{file_id}/finalize",
        headers={
            **common,
            "Expected-Revision": str(chunk["revision"]),
            "Idempotency-Key": f"docker-finalize-{filename}",
        },
    )


def wait_status(
    base_url: str,
    path: str,
    secret: str,
    terminal: set[str],
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = request_json(
                base_url,
                "GET",
                path,
                headers={"X-Tara-Job-Secret": secret},
            )
        except RuntimeError as exc:
            if "HTTP 429" not in str(exc):
                raise
            time.sleep(0.5)
            continue
        if last.get("status") in terminal:
            return last
        time.sleep(0.25)
    raise RuntimeError(f"timeout waiting for {path}: {last}")


def compose(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", "compose", *_COMPOSE_FILES, *args],
            check=True,
            text=True,
            capture_output=True,
            env=env,
            cwd=ROOT,
            timeout=600,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {detail}") from exc


def available_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def available_docker_subnet() -> str:
    listed = subprocess.run(
        ["docker", "network", "ls", "-q"],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
    ).stdout.split()
    used: list[ipaddress.IPv4Network] = []
    if listed:
        inspected = subprocess.run(
            ["docker", "network", "inspect", *listed],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
        for network in json.loads(inspected.stdout):
            configurations = network.get("IPAM", {}).get("Config") or []
            for config in configurations:
                try:
                    candidate = ipaddress.ip_network(config.get("Subnet", ""))
                except ValueError:
                    continue
                if isinstance(candidate, ipaddress.IPv4Network):
                    used.append(candidate)
    start = (os.getpid() % 254) + 1
    octets = list(range(start, 255)) + list(range(1, start))
    for third_octet in octets:
        candidate = ipaddress.ip_network(f"172.30.{third_octet}.0/29")
        if not any(candidate.overlaps(existing) for existing in used):
            return str(candidate)
    raise RuntimeError("no private Docker subnet is available for the smoke test")


def prepare_secrets(environment: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="tara-docker-audio-secrets-"))
    certificate = root / "tls.crt"
    private_key = root / "tls.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    backup_key = root / "backup-signing-key"
    modal_id = root / "modal-token-id"
    modal_secret = root / "modal-token-secret"
    backup_key.write_text(os.urandom(32).hex(), encoding="ascii")
    modal_id.write_text("docker-audio-smoke-id", encoding="ascii")
    modal_secret.write_text("docker-audio-smoke-secret", encoding="ascii")
    for path in (certificate, private_key, backup_key, modal_id, modal_secret):
        path.chmod(0o444)
    environment.update(
        {
            "TARA_WEB_TLS_CERTIFICATE_FILE": str(certificate),
            "TARA_WEB_TLS_PRIVATE_KEY_FILE": str(private_key),
            "TARA_WEB_BACKUP_KEY_FILE": str(backup_key),
            "MODAL_TOKEN_ID_FILE": str(modal_id),
            "MODAL_TOKEN_SECRET_FILE": str(modal_secret),
        }
    )
    return root


def run_playwright(base_url: str, environment: dict[str, str]) -> None:
    browser_environment = environment.copy()
    browser_environment.update(
        {
            "E2E_BASE_URL": base_url,
            "E2E_IGNORE_HTTPS_ERRORS": "1",
        }
    )
    subprocess.run(
        [
            "npx",
            "--no-install",
            "playwright",
            "test",
            "e2e/real/compose-vertical.spec.ts",
            "--project=chromium",
        ],
        check=True,
        cwd=ROOT / "webinterface" / "frontend",
        env=browser_environment,
        timeout=600,
    )


def run(
    base_url: str | None, *, playwright: bool = False, build: bool = True
) -> None:
    InferenceHandler.requests_seen = 0
    server = ThreadingHTTPServer(("0.0.0.0", 0), InferenceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = os.environ.copy()
    if base_url is None:
        host_port = available_port()
        base_url = f"https://127.0.0.1:{host_port}"
    else:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
        ):
            raise ValueError("base URL must be https://127.0.0.1:<port>")
        host_port = parsed.port
    environment.update(
        {
            "COMPOSE_PROJECT_NAME": f"tara-audio-smoke-{os.getpid()}",
            "TARA_WEB_HOST_PORT": str(host_port),
            "TARA_WEB_INTERNAL_SUBNET": available_docker_subnet(),
            "TARA_WEB_PUBLIC_URL": base_url,
            "TARA_INFERENCE_ENDPOINT": (
                f"http://host.docker.internal:{server.server_port}"
            ),
            "TARA_INFERENCE_AUTH_PROVIDER": "none",
            "TARA_TRANSCRIPTION_PARALLELISM": "2",
        }
    )
    secret_root = prepare_secrets(environment)
    try:
        up_arguments = ["up", "-d"]
        if build:
            up_arguments.append("--build")
        up_arguments.extend(
            (
                "--force-recreate",
                "tara-web-init",
                "tara-web",
                "tara-proxy",
            )
        )
        compose(*up_arguments, env=environment)
        deadline = time.monotonic() + 60
        while True:
            try:
                request_json(base_url, "GET", "/api/v1/ready")
                break
            except (OSError, RuntimeError) as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Docker deployment did not become ready"
                    ) from exc
                time.sleep(0.5)

        with tempfile.TemporaryDirectory(prefix="tara-docker-audio-") as directory:
            root = Path(directory)
            mp3 = generate_audio(root / "Alice.mp3", "libmp3lame")
            ogg = generate_audio(root / "MaitreDuJeu.ogg", "libvorbis")
            session = request_json(
                base_url,
                "POST",
                "/api/v1/uploads/sessions",
                headers={"Idempotency-Key": f"docker-smoke-{time.time_ns()}"},
            )
            session_id, secret = session["session_id"], session["secret"]
            upload(base_url, session_id, secret, "Alice.mp3", "audio/mpeg", mp3)
            upload(base_url, session_id, secret, "MaitreDuJeu.ogg", "audio/ogg", ogg)
            ready = wait_status(
                base_url, f"/api/v1/sessions/{session_id}", secret, {"ready"}, 45
            )
            launched = request_json(
                base_url,
                "POST",
                f"/api/v1/sessions/{session_id}/jobs",
                headers={
                    "X-Tara-Job-Secret": secret,
                    "Expected-Revision": str(ready["revision"]),
                    "Idempotency-Key": f"docker-job-{time.time_ns()}",
                },
            )
            job_id = launched["job_id"]
            job = wait_status(
                base_url,
                f"/api/v1/jobs/{job_id}",
                secret,
                {"completed", "failed", "cancelled", "timed_out"},
                90,
            )
            if job["status"] != "completed":
                raise RuntimeError(f"Docker audio job failed: {job}")
            result = request_json(
                base_url,
                "GET",
                f"/api/v1/jobs/{job_id}/result",
                headers={"X-Tara-Job-Secret": secret},
            )
            if result.get("status") != "available":
                raise RuntimeError(f"Docker result is unavailable: {result}")
            public_payload = json.dumps(result, ensure_ascii=True)
            forbidden = (
                "/data",
                "work/",
                "host.docker.internal",
                secret,
                "docker-smoke",
            )
            if any(value in public_payload for value in forbidden):
                raise RuntimeError("Docker result leaked an internal value")

            inspect_code = (
                "import json,os; from pathlib import Path; "
                "from tara.schemas.registry import load_merged_transcription; "
                "p=Path('/data/runtime/jobs')/os.environ['JOB_ID']/"
                "'work/audio/transcriptions/merged_transcription.yaml'; "
                "m=load_merged_transcription(p); "
                "print(json.dumps({'speakers':sorted({s.author.speaker "
                "for s in m.content.segments}),"
                "'sources':len({s.author.source_file for s in m.content.segments})}))"
            )
            inspected = compose(
                "exec",
                "-T",
                "-e",
                f"JOB_ID={job_id}",
                "tara-web",
                "python",
                "-c",
                inspect_code,
                env=environment,
            )
            attribution = json.loads(inspected.stdout.strip())
            if attribution != {
                "speakers": ["Alice", "MaitreDuJeu"],
                "sources": 2,
            }:
                raise RuntimeError(f"unexpected attribution: {attribution}")
            if InferenceHandler.requests_seen != 2:
                raise RuntimeError("expected exactly two inference requests")
            print(f"Docker audio smoke passed for {job_id}")
        if playwright:
            run_playwright(base_url, environment)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        try:
            compose("down", "-v", "--remove-orphans", env=environment)
        finally:
            shutil.rmtree(secret_root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--playwright", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    run(
        args.base_url.rstrip("/") if args.base_url else None,
        playwright=args.playwright,
        build=not args.no_build,
    )


if __name__ == "__main__":
    main()
