# Tara

Tara turns recordings of tabletop role-playing sessions into a structured session
report. It transcribes participants' audio, merges the transcripts into a timeline,
and analyzes that timeline to produce a readable summary with supporting evidence.
You can also start from an existing merged transcription.

The repository contains the command-line application, a local transcription
server, a web interface, and the configuration needed to run them. The web
interface accepts audio tracks, a merged transcription in YAML, or a ZIP of audio
tracks, then lets users follow the job and read or download its report.

## What Tara does

1. **Transcription:** `src/inference_server/` serves local speech-to-text requests.
   Tara can also use Modal for Parakeet transcription.
2. **Session preparation:** `src/tara/transcription.py` orders and merges the
   participant transcripts into `merged_transcription.yaml`.
3. **Analysis:** `src/tara/analysis/` builds a scene timeline and a local evidence
   index. Specialist agents collect facts on events, characters, combat, quests,
   and uncertainty. A shared blackboard resolves conflicts before composition
   and audit of the report.
4. **Results:** A run writes `session_summary.md` and a structured
   `session_summary.yaml`. The web interface publishes a restricted result
   schema; private transcript and analysis artifacts stay in the run directory.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the component boundaries and
[docs/schemas](docs/schemas) for the input and public-result formats.

## Quick start

Python 3.13 and [`uv`](https://docs.astral.sh/uv/) are required for the Python
application. Run these commands from the repository root:

```bash
uv sync --frozen
uv run tara --merged-transcription /path/to/merged_transcription.yaml
```

To process audio, install the inference dependencies and use the launcher for
your platform. It can start the bundled local transcription server:

```bash
uv sync --frozen --extra inference
PYTHON_EXE=.venv/bin/python bash run_tara.sh --audio-dir /path/to/recording
```

On Windows, use `run_tara.bat --audio-dir "C:\path\to\recording"`. To use Modal
instead of the local server, install the `deploy` extra and follow
[the Modal guide](docs/modal_inference.md).

The CLI also accepts `--context` and `--prior-context` for campaign information.
The current transcription remains the source for session events. Run
`uv run tara --help` for the available options.

## Web interface

The web application lives in `src/tara_web/` and `webinterface/frontend/`.
Docker Compose is the supported deployment path; the native server is intended
for local development. Start with the [web guide](docs/webinterface/launching.md)
and [deployment instructions](docs/webinterface/docker-deployment.md).

## Configuration and formats

The main configuration is [config/configuration.yaml](config/configuration.yaml).
Tara uses YAML for configuration, prompts, and analysis artifacts. It accepts JSON
for supported older input files; HTTP APIs and external tools use their own JSON
contracts. The local API in `src/tara/server.py` exposes `/health`, `/v1/runs`,
and `/v1/analysis`.

Configuration, audio, transcripts, reports, logs, local databases, and secrets
may contain private session data. Keep generated artifacts out of version
control. The project `.env` file, if used, belongs at the repository root.

## Repository map

| Path | Purpose |
| --- | --- |
| `src/tara/` | CLI, transcription, analysis, configuration, and API |
| `src/inference_server/` | Local speech-to-text service |
| `src/tara_web/` | Web API, jobs, storage, and administration |
| `webinterface/frontend/` | User-facing web application |
| `config/` | Versioned example and default configuration |
| `docs/` | Schemas, operations, and deployment guides |
| `tests/` | Automated checks |

For development, install `uv sync --frozen --extra dev` and run `uv run pytest`
and `uv run ruff check .` from the repository root. Web-specific checks and
frontend commands are in [webinterface/README.md](webinterface/README.md).
