"""Feature test script that launches the server locally and calls the endpoint (streaming)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

import requests

SERVER_URL: Final[str] = "http://localhost:8000"
HEALTH_URL: Final[str] = f"{SERVER_URL}/health"
TRANSCRIBE_URL: Final[str] = f"{SERVER_URL}/v1/audio/transcriptions"


def wait_for_health(timeout: float = 30.0, proc: subprocess.Popen[str] | None = None) -> None:
    """Poll the health endpoint until it responds or timeout expires."""

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc and proc.poll() is not None:
            raise RuntimeError("Server process exited before becoming healthy.")
        try:
            resp = requests.get(HEALTH_URL, timeout=2)
            if resp.status_code == 200:
                return
        except requests.RequestException:
            time.sleep(0.5)
    raise TimeoutError(f"Server did not become healthy within {timeout} seconds")


def print_gpu_usage(label: str) -> None:
    """Print GPU memory usage using nvidia-smi when available."""

    cmd = [
        "nvidia-smi",
        "--query-gpu=memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            print(f"{label}: nvidia-smi returned no data")
            return
        readings = []
        for line in lines:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 2:
                continue
            used, total = parts
            readings.append(f"{used}/{total} MiB")
        if readings:
            print(f"{label}: GPU memory usage: {'; '.join(readings)}")
        else:
            print(f"{label}: unable to parse nvidia-smi output")
    except FileNotFoundError:
        print(f"{label}: nvidia-smi not found; skipping GPU memory check")
    except Exception as exc:  # pragma: no cover - observational only
        print(f"{label}: unable to read GPU memory ({exc})")


def main() -> None:
    audio = Path(os.environ.get("TARA_SAMPLE_AUDIO", "unit_4_body.mp3")).expanduser()
    if not audio.exists():
        raise FileNotFoundError(f"Sample audio not found: {audio}")

    base_dir = Path(__file__).resolve().parents[2]  # Tara/
    project_root = base_dir.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(base_dir / "src") + os.pathsep + env.get("PYTHONPATH", "")
    if os.name == "nt":
        # Avoid duplicate OpenMP runtime abort on Windows when faster-whisper loads.
        env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    # Force release after each request to free GPU memory (may crash if backend release is unstable).
    env["INFERENCE_FORCE_RELEASE"] = "1"
    env.pop("INFERENCE_DISABLE_RELEASE", None)

    # Start uvicorn server in a subprocess
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "inference_server.app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--log-level",
        "info",
    ]
    print(f"Starting server: {' '.join(cmd)}")
    server = subprocess.Popen(cmd, cwd=project_root, env=env)

    def _call_transcription(label: str) -> None:
        print(f"\n=== Request {label} ===")
        headers: dict[str, str] = {}  # Add Authorization if your server requires it.
        data = {"model": "large-v3", "language": "fr", "stream": "true"}
        with audio.open("rb") as handle:
            files = {"file": (audio.name, handle, "audio/mpeg")}
            with requests.post(
                TRANSCRIBE_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=300,
                stream=True,
            ) as response:
                response.raise_for_status()
                print("Streaming response:")
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    if line.startswith("data:"):
                        payload = json.loads(line.removeprefix("data:").strip())
                        print(json.dumps(payload, ensure_ascii=False))
    try:
        print_gpu_usage("Before health check")
        wait_for_health(proc=server)
        print_gpu_usage("After health check")
        _call_transcription("A")
        print_gpu_usage("After request A")
        time.sleep(2)
        _call_transcription("B")
        print_gpu_usage("After request B")
    finally:
        print("Waiting for 10 seconds before stopping server...")
        time.sleep(10)
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        print("Server process stopped.")
        print_gpu_usage("After server stop")


if __name__ == "__main__":
    main()

