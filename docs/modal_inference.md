# Modal inference server

This document describes how Tara runs Parakeet v3 transcription on Modal GPUs
using **`Function.spawn()`** (`modal_map`) — the default `--modal` path.

## Architecture

| Path | Mechanism | Auth |
|---|---|---|
| **`modal_map` (default `--modal`)** | `transcribe_one` spawned per file; Modal autoscales containers | `modal setup` only |
| **`modal_proxy` (legacy HTTP)** | OpenAI-compatible HTTP POST to `@modal.asgi_app` | Proxy auth tokens |
| **Local** | uvicorn inference server | none |

```text
Tara local                         Modal cloud
-----------                        -----------
discover audio files    spawn(all)   Modal queue + autoscale (max 3 containers)
poll + progress logs               ParakeetTranscriber.transcribe_one(jobN)
write transcriptions/*.json        GPU memory snapshot on cold start
containers idle → autoscaler scale-down (10s)
```

Primary deployment entry point: [`modal_inference.py`](../modal_inference.py)

- **`ParakeetTranscriber`** (`@app.cls`) — GPU class with `@modal.method transcribe_one`
  (`max_containers=3`, `buffer_containers=0`, `scaledown_window=10`, GPU memory snapshots)
- **`warm_cache`** — CPU function to pre-populate the model Volume
- **`fastapi_app`** — dev/legacy HTTP only (`modal serve`, `max_containers=5`)

## GPU memory snapshots

Production `modal_map` uses **`ParakeetTranscriber`**, a Modal class with GPU memory
snapshots enabled (`enable_memory_snapshot=True`,
`experimental_options={"enable_gpu_snapshot": True}`).

```text
Volume (warm_cache)          Memory snapshot (Modal platform)
/model-cache/nemo-extract    RAM + GPU VRAM after Parakeet load + warmup
         │                              │
         └──────── cold start ──────────┘
                    restore → transcribe_one
```

Lifecycle:

1. **`@modal.enter(snap=True)`** — load Parakeet on CUDA from the Volume, run a 1s
   silence warmup (captured in the snapshot).
2. **`@modal.enter(snap=False)`** — light restore hook (re-import `torch` if needed).
3. **`@modal.method transcribe_one`** — reuse the preloaded `ParakeetBackend`.

Flags in [`modal_inference.py`](../modal_inference.py):

| Constant | Default | Role |
|---|---|---|
| `ENABLE_MEMORY_SNAPSHOT` | `True` | CPU/GPU snapshot infrastructure |
| `ENABLE_GPU_SNAPSHOT` | `True` | Capture GPU VRAM (alpha); set `False` for CPU-only snapshot fallback |
| `_STARTUP_TIMEOUT_SECONDS` | `2400` | First snapshot creation can be slow |

**Deploy is required** before GPU snapshots work (`modal deploy modal_inference.py`).
`modal run` alone is not sufficient for snapshot validation.

After deploy, check the Modal app **Containers** tab or logs for
`Snapshot created` / `Restoring Function from memory snapshot`.

### Troubleshooting snapshots

| Symptom | Action |
|---|---|
| Snapshot creation fails (CUDA / NeMo) | Set `ENABLE_GPU_SNAPSHOT=False`, redeploy (CPU snapshot + `move_models_to_device("cuda")` on restore) |
| Restore OK but inference fails | Redeploy; check NeMo logs; try `torch` re-import in `setup_runtime` |
| No timing improvement vs baseline | See [Post-deploy benchmark](#post-deploy-benchmark) below; consider disabling snapshot if within noise |

Do **not** run `modal container stop` while a Tara `python -m tara` job is still
polling — let the run finish (~7 min for Record10).

## Install Modal locally

From the `TaraRepo` directory:

```powershell
uv sync --extra deploy
uv run modal setup
```

If you do not use `uv`, install Modal in your active Python environment:

```powershell
pip install "modal>=1.0"
modal setup
```

## Deploy and warm model cache

```powershell
cd $env:USERPROFILE\Documents\Projets\DM_Assistant\TaraRepo
.\deploy_modal.bat
```

```bash
cd /path/to/TaraRepo
./deploy_modal.sh
```

Each script runs:

1. `modal deploy modal_inference.py` — deploy or update the inference app.
2. `modal run modal_inference.py::warm_cache_entrypoint` — pre-populate the
   shared model cache on CPU.

Retry warm manually:

```powershell
uv run --extra deploy modal run modal_inference.py::warm_cache_entrypoint
```

### What warm cache does

The `warm_cache` Modal function:

1. Checks whether Parakeet v3 is already fully extracted under
   `/model-cache/nemo-extract` (skip-if-complete).
2. If needed, downloads `nvidia/parakeet-tdt-0.6b-v3` from Hugging Face into
   `/model-cache/huggingface` using the pinned revision in `modal_inference.py`
   (`PARAKEET_HF_REVISION`).
3. Extracts the `.nemo` archive into `/model-cache/nemo-extract`.
4. Calls `cache_volume.commit()` so other containers see the cached files.

## Run Tara with Modal (recommended)

No inference endpoint URL or proxy tokens are required.

```powershell
.\run_tara.bat --audio-dir C:\path\to\recording --modal
```

```bash
./run_tara.sh --audio-dir /path/to/recording --modal
```

`--modal` sets:

- `TARA_INFERENCE_AUTH_PROVIDER=modal_map`
- `TARA_TRANSCRIPTION_PARALLELISM=all` (when not already defined)

Launchers run preflight checks before Tara starts:

1. Modal CLI is installed (`uv sync --extra deploy`)
2. Modal credentials are configured (`modal setup`)
3. Deployed class exists (`tara-parakeet-inference.ParakeetTranscriber.transcribe_one`)

Optional configuration in `config/configuration.json`:

```json
"transcription": {
  "inference_auth_provider": "modal_map",
  "parallelism": 0,
  "modal_progress_interval_seconds": 3,
  "modal_max_audio_bytes": 104857600,
  "modal_max_containers": 3,
  "modal_files_per_container": 2
}
```

Environment overrides:

- `TARA_MODAL_PROGRESS_INTERVAL_SECONDS` — average progress log interval (default `3`)
- `TARA_MODAL_MAX_AUDIO_BYTES` — inline audio payload limit per file (default 100 MiB)
- `TARA_MODAL_QUEUE_GRACE_SECONDS` — max wait while a job is queued or cold-starting (default `1800`)

### Timeouts

Modal map uses **two independent client-side timeouts** per file:

| Phase | Config | Default | Applies when |
|---|---|---|---|
| Queue / cold start | `modal_queue_grace_seconds` | 1800s (30 min) | Job spawned but not yet transcribing |
| Active transcription | `request_timeout_seconds` | 1800s (30 min) | First chunk progress or `running` status |

Time spent waiting in Modal's queue **does not** consume the transcription timeout. If containers are slow to start, increase `modal_queue_grace_seconds` (for example `3600` for 60 minutes).

Example:

```json
"transcription": {
  "request_timeout_seconds": 3600,
  "modal_queue_grace_seconds": 3600
}
```

## Scaling and parallelism

Tara submits **every** ``Function.spawn()`` job immediately. Modal queues inputs and
autoscales containers up to **`max_containers=3`** on the deployment, with
**`buffer_containers=0`** (scale on demand; no idle warm pool).

| Setting | Role on ``modal_map`` |
|---|---|
| ``modal_max_containers`` | Informational default aligned with deployment ``max_containers`` |
| ``modal_files_per_container`` | Planning hint for expected concurrency (`ceil(files / ratio)`) |
| ``TARA_TRANSCRIPTION_PARALLELISM`` | Does **not** batch client spawns; HTTP/local path only |

Example: 6 files → all 6 jobs are queued immediately; Modal runs **3 at a time**
and holds the rest in its queue. Raise deployment ``max_containers`` only if you
need more parallel GPUs.

Config / env:

- `modal_files_per_container` / `TARA_MODAL_FILES_PER_CONTAINER` (default `2`)
- `modal_max_containers` / `TARA_MODAL_MAX_CONTAINERS` (default `3`, matches deployment)

During a run, verify scaling:

```powershell
uv run --extra deploy modal container list --json
uv run --extra deploy modal app logs tara-parakeet-inference -f --show-container-id --timestamps
```

Tara logs average chunk progress every few seconds:

```text
Modal transcription progress: 38.5% average (6 files, 4 active, 1 complete)
```

Progress uses a run-scoped `modal.Dict` (`tara-progress-{run_id}`) updated from
chunk callbacks inside `transcribe_one`. It is deleted after each run.

## Container idle shutdown

GPU containers shut down automatically via Modal's autoscaler
([Scaling guide](https://modal.com/docs/guide/scale)). Both `transcribe_one` and
`fastapi_app` set **`scaledown_window=10`**: after a container finishes its last
job and stays idle, Modal scales it down within about 10 seconds.

Tara does **not** call `modal container stop` after transcription. That keeps
concurrent Tara runs safe — one run cannot terminate containers still serving
another run.

Tara still collects `MODAL_TASK_ID` values from each job for debug logging only.
The [`modal container`](https://modal.com/docs/cli/latest/container) CLI remains
available for manual inspection (`list`, `logs`) or emergency stop.

**Trade-off:** a 10 s idle window limits GPU billing after work completes. If the
same container sits idle for more than 10 s between consecutive jobs, the next job
may cold-start.

During or after a run, inspect containers:

```powershell
uv run --extra deploy modal container list --json
```

After all jobs finish, running containers should drain within roughly 10–20 s.
Modal may shut down sooner when the app is overprovisioned.

## Billing report

At the end of a `--modal` run, `run_tara.bat` prints a Modal billing report:

```powershell
uv run --extra deploy modal billing report --start 2026-06-19 --end 2026-06-20 --resolution h --tz local
```

## Manual smoke test (map path)

Requires a prior **`modal deploy`** (GPU snapshots need a deployed app):

```powershell
uv run --extra deploy modal deploy modal_inference.py
uv run --extra deploy modal run modal_inference.py::smoke_test --audio-path C:\path\to\sample.wav
```

## Post-deploy benchmark

Compare Record10 timings against the point-0 baseline after enabling snapshots.

**1. Deploy and warm cache** (if not already done):

```powershell
.\deploy_modal.bat
```

**2. Run Record10** (let finish completely, ~7 min — do not stop containers):

```cmd
cmd /c "call %USERPROFILE%\anaconda3\condabin\conda.bat activate DM && set PYTHONPATH=%USERPROFILE%\Documents\Projets\DM_Assistant\TaraRepo\src && set TARA_INFERENCE_AUTH_PROVIDER=modal_map && cd /d %USERPROFILE%\Documents\Projets\DM_Assistant\TaraRepo && python -m tara --audio-dir %USERPROFILE%\Documents\Projets\DM_Assistant\Record_session\Record10 --skip-analysis > %USERPROFILE%\Documents\Projets\DM_Assistant\TaraRepo\tmp\timing_record10_post_snapshot.log 2>&1"
```

On first deploy with snapshots, run once to create snapshots, wait ~20 s for
scale-down, then run again and compare the **second** log.

**3. Compare to point-0 baseline** by reading `tmp/timing_record10_post_snapshot.log`
manually (no helper script — temporary validation only):

| Metric | Where to look |
|---|---|
| Total wall time | `Running transcription stage` → `Running processing stage` |
| Per-file Modal time | `Finished transcription X/6: … (Ys on Modal)` |
| Queue waits | `still queued … (Ns waiting for a container)` |
| Max active | max `N active` in `Modal transcription progress` lines |

Point-0 reference (`tmp/timing_record10_v2.log`): wall ~7 min 13 s, cold Modal
189–192 s, warm Modal 139–142 s, worst handoff ~40 s, max active 3.

**Keep GPU snapshot** if cold times or worst handoff improve meaningfully (≥15–20%
or ≥10 s). **Disable** (`ENABLE_GPU_SNAPSHOT=False`, redeploy) if deltas are within
run-to-run noise (~±5%).

## Serve for development (HTTP / legacy)

```powershell
uv run modal serve modal_inference.py
```

Modal prints a temporary web URL. The ASGI endpoint requires proxy auth headers
and is **not** used by `--modal`.

## Legacy HTTP path (`modal_proxy`)

The previous HTTP + proxy-token path remains available for backward compatibility.

Set tokens in the current shell session:

```powershell
$env:TARA_INFERENCE_ENDPOINT="https://<workspace>--tara-parakeet-inference-fastapi-app.modal.run"
$env:TARA_INFERENCE_AUTH_PROVIDER="modal_proxy"
$env:TARA_MODAL_PROXY_AUTH_KEY="wk-..."
$env:TARA_MODAL_PROXY_AUTH_SECRET="ws-..."
.\run_tara.bat --audio-dir C:\path\to\recording --inference-auth-provider modal_proxy
```

Security notes for proxy tokens:

- Do not commit them.
- Do not put real values in `config/configuration.json`.
- Prefer temporary environment variables for one run.

Health check with headers:

```powershell
Invoke-RestMethod `
  -Headers @{
    "Modal-Key" = $env:TARA_MODAL_PROXY_AUTH_KEY
    "Modal-Secret" = $env:TARA_MODAL_PROXY_AUTH_SECRET
  } `
  -Uri "$env:TARA_INFERENCE_ENDPOINT/health"
```

HTTP streaming progress (`transcription.streaming_enabled`) applies to local and
`modal_proxy` runs only. The `modal_map` path uses aggregated chunk progress
instead.

Both `modal_map` and `modal_proxy` rely on the same Modal autoscaler idle
shutdown (`scaledown_window=10`); Tara does not force-stop containers after
transcription.

## Notes

- `HF_XET_HIGH_PERFORMANCE=1` is enabled in the Modal image for faster Hugging
  Face downloads during warm.
- Audio files larger than 100 MiB must use the legacy HTTP path or be split.
- Inspect the Volume after warm:
  `uv run --extra deploy modal volume ls tara-parakeet-cache`
- To upgrade cached weights, edit `PARAKEET_HF_REVISION` in
  [`modal_inference.py`](../modal_inference.py), redeploy, and re-run warm cache.
