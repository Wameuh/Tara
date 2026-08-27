# Inference Server

FastAPI service exposing an OpenAI-compatible transcription endpoint used by the TARA UI (`TranscriptionService` remote backend). Supports multiple ASR backends (Faster-Whisper and NeMo Parakeet), JSON and streaming (SSE) responses, optional worker subprocess isolation, and GPU/CPU selection.

## Backends

The server supports two ASR backends:

- **Faster-Whisper**: Default backend for Whisper-based models (e.g., `large-v3`, `medium`, `small`)
- **Parakeet**: NeMo Parakeet ASR models (use `parakeet:` prefix, e.g., `parakeet:nvidia/parakeet-tdt-0.6b-v3`)

The backend is automatically selected based on the model name prefix.

## Components

- `app.py`: FastAPI app with `/health` and `/v1/audio/transcriptions`, SSE streaming, worker orchestration, temp-file handling.
- `backend.py`: `TranscriptionBackend` protocol and factory function for backend selection.
- `base_backend.py`: Abstract base class for transcription backends.
- `faster_whisper_backend.py`: Faster-Whisper backend implementation.
- `parakeet_backend.py`: Parakeet backend implementation.
- `parakeet_utils.py`: Utility functions for Parakeet (audio conversion, chunking, overlap merging).
- `worker.py`: subprocess runner used when isolation is enabled (default on Windows).
- `models.py`: Pydantic response models.
- `examples/`: Example client scripts for calling the transcription API.

## Quick Start

From the **TaraRepo** root (this package lives under `src/inference_server/`):

```powershell
conda activate DM
$env:PYTHONPATH = "src"
export INFERENCE_BEARER_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m uvicorn inference_server.app:app --host 127.0.0.1 --port 8000
```

Or use `run_tara.bat --audio-dir ...`, which sets `PYTHONPATH` and starts this server before `python -m tara`.

## API (OpenAI-compatible)

- `POST /v1/audio/transcriptions`
  - Form fields: `file` (required), `model` (required), `language` (optional), `response_format` (only `json`), `stream` (`true` for SSE).
  - Returns JSON with `text`, `segments[{start,end,text}]`, `language`, `duration`, `model`.
  - If `stream=true`, emits SSE events: repeated `segment` payloads with optional `progress`, then a final `final` payload.
- `GET /health` → `{ "status": "ok" }`.

### Model Selection

The `model` parameter determines which backend is used:

**Faster-Whisper models** (no prefix):
- `large-v3`, `medium`, `small`, `base`, `tiny`

**Parakeet models** (must start with `"parakeet:"` prefix):
- `parakeet:nvidia/parakeet-tdt-0.6b-v3`
- Any NeMo ASR model identifier

### Examples

**Faster-Whisper:**
```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $INFERENCE_BEARER_TOKEN" \
  -F "file=@path/to/audio.wav" \
  -F "model=large-v3" \
  -F "language=fr"
```

**Parakeet:**
```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $INFERENCE_BEARER_TOKEN" \
  -F "file=@path/to/audio.wav" \
  -F "model=parakeet:nvidia/parakeet-tdt-0.6b-v3" \
  -F "language=fr"
```

**Streaming (works with both backends):**
```bash
curl -N -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $INFERENCE_BEARER_TOKEN" \
  -F "file=@path/to/audio.wav" \
  -F "model=large-v3" \
  -F "language=fr" \
  -F "stream=true"
```

## Installation & Dependencies

### Base Dependencies

```bash
pip install fastapi uvicorn
```

### Faster-Whisper Backend

```bash
pip install faster-whisper
```

### Parakeet Backend

```bash
# NeMo toolkit with ASR support
pip install nemo_toolkit[asr]
# or
pip install nemo[asr]

# Audio processing libraries (required for format conversion)
pip install librosa soundfile
# OR install ffmpeg system-wide as fallback:
# - Windows: Download from https://ffmpeg.org/download.html
# - Linux: sudo apt-get install ffmpeg
# - macOS: brew install ffmpeg
# - Conda: conda install -c conda-forge ffmpeg
```

**Note:** At least one audio conversion method (librosa or ffmpeg) must be available for the Parakeet backend to function.

## Configuration & Env Vars

- `INFERENCE_BEARER_TOKEN` or `INFERENCE_BEARER_TOKEN_FILE`: required bearer
  credential for transcription requests. `run_tara.sh` generates an ephemeral
  token for a local server when one is not supplied.
- `INFERENCE_ALLOWED_MODELS`: comma-separated allowlist. Defaults to the models
  documented above; arbitrary provider model identifiers are rejected.
- `INFERENCE_MAX_UPLOAD_BYTES`: maximum audio bytes copied per request (default
  1 GiB). Requests also require a bounded `Content-Length` before multipart parsing.
- `INFERENCE_MAX_CONCURRENT_REQUESTS`: process-wide admitted requests (default 2).
- `INFERENCE_USE_WORKER`: `1` to force worker subprocess; defaults to `1` on Windows, `0` elsewhere.
- `INFERENCE_WORKER_TIMEOUT_SECONDS`: Worker timeout in seconds (default `900`).
- `INFERENCE_DISABLE_RELEASE`: skip backend release after requests (default off).
- `INFERENCE_FORCE_RELEASE`: force release even on Windows (default off).
- `INFERENCE_LOG_LEVEL`: logging level (default `INFO`).

**Faster-Whisper Backend:**
- Backend defaults: `device=cuda`, `compute_type=float16`, `beam_size=5`

**Parakeet Backend:**
- `PARAKEET_CHUNK_SIZE`: Override default chunk size in seconds (default: 400.0)
- `PARAKEET_OVERLAP_PERCENTAGE`: Override default overlap percentage (default: 5.0)
- `TARA_NEMO_EXTRACT_DIR`: Persistent NeMo extract cache (Modal: `/model-cache/nemo-extract`)
- `HF_HOME`: Hugging Face download cache (Modal: `/model-cache/huggingface`)

On Modal, run `deploy_modal.bat` or `deploy_modal.sh` from `TaraRepo` to deploy
and pre-warm the Parakeet cache on the `tara-parakeet-cache` Volume. See
`docs/modal_inference.md`.

## Audio Format Support

### Parakeet Backend

The Parakeet backend automatically converts all audio to mono 16kHz WAV format (required by NeMo models):

- **Input formats**: Any format supported by librosa or ffmpeg (MP3, WAV, OGG, FLAC, M4A, AAC, etc.)
- **Automatic conversion**:
  - Stereo → Mono (automatic)
  - Any sample rate → 16kHz (automatic)
  - All formats → WAV (temporary file for processing)
- **No manual preprocessing required** - the backend handles all format conversion automatically

### Faster-Whisper Backend

Faster-Whisper supports a wide range of audio formats natively, but may have format-specific performance characteristics.

## Design Notes

- Temp files are written per request and cleaned via `BackgroundTasks`.
- Worker mode isolates model execution to release VRAM/process resources after each call; the parent accepts only bounded JSON frames over a one-way pipe.
- Model caching occurs inside each backend; `release_all()` frees weights when allowed.
- Error handling returns 400 for unsupported response formats and 500 for backend issues with request IDs logged.
- Intended to back the TARA UI via the remote inference path (`transcription.inference_endpoint`).
- Backend selection is automatic based on model name prefix - no manual configuration needed.
- Parakeet backend uses chunked transcription for large files (400s chunks by default) to manage memory efficiently.

## Tests

```bash
cd Tara
pytest tests/inference_server/ --cov=src/inference_server
```
