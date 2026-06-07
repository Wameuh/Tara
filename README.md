# TaraRepo

TaraRepo is the standalone implementation workspace for the new Tara analysis
pipeline described in `ARCHITECTURE.md`.

The project now uses a scene-driven blackboard architecture: scene boundaries
and long scene descriptions provide the narrative timeline, then local
retrieval, specialist agents, arbitration, and adversarial audit enrich and
control the final summary. The old `Tara` project remains the source of
reference contracts for transcription, processing, configuration patterns,
logging, telemetry, usage reporting, and local runner behavior.

## Migration Guardrails

- Do not modify the existing `../Tara`
  transcription or processing code as part of this refactor. The transcription
  **inference server** is copied into TaraRepo as `src/inference_server/`; keep
  parity fixes in TaraRepo unless you intentionally upstream them to legacy Tara.
- Treat `merged_transcription.json` as the canonical input to the new analysis
  pipeline.
- Support a full local run from audio by calling the configured transcription
  inference server, then processing into `merged_transcription.json`, then
  running the new analysis.
- Keep API and Cursor CLI LLM execution hidden behind `LLMRunner`; implement and
  validate the API backend first.
- Do not commit `.env`, credentials, tokens, private audio, private
  transcriptions, logs, caches, local databases, or runtime artifacts.
- Run the review and commit safety gate after every roadmap task before moving
  to the next task.

## Review and commit safety

Operational workflow for Composer 2 subagents, staged-file checks, and report
paths lives in the Cursor skill **tara-review** (`.cursor/skills/tara-review/`):
see `.cursor/skills/tara-review/review_process/review_agents.md`, report template,
and `reviews/` under that path. Load the skill when running reviews. Before every
commit, run `git status --short`, stage explicit paths, inspect `git diff --cached`,
and refuse anything that looks like secrets, `.env`, private media, or runtime
analysis outputs.

## Reference Inputs

`Record_session/Record19/transcriptions/merged_transcription.json` is the manual
evaluation reference for the first migration pass. It is private runtime data and
must not be copied into this repository unless explicitly sanitized or
whitelisted later.

## Current Status

Task `00` documents the migration boundaries, preserved contracts, and privacy
guardrails. Task `01` is represented by the module mapping roadmap in
`tmp/tasks/01_module_mapping.md`. Task `02` adds the first implementation slice:
the shared LLM runner with API and Cursor CLI backends behind one interface.
Task `03` adds the Pydantic model layer and core analysis invariants.
Task `04` adds local CPU evidence chunking and lexical retrieval over merged
transcriptions.
Tasks `05`-`10` add the deterministic planner, specialists, blackboard,
arbitration, composer, audit, patching, and bounded audit loop.
The analysis package also includes the scene-driven enrichment layer under
`src/tara/analysis/scenes/`: it identifies narrative scene boundaries, writes
per-scene transcript slices, generates long scene descriptions, and injects
scene-derived facts into the blackboard before specialist retrieval runs.
Task `11` wires the standalone CLI, JSON configuration, `.env` loading,
transcription-server integration, processing, FastAPI endpoints, and final
`session_summary.md` / `session_summary.json` generation.
Task `12` adds acceptance metrics, validates those metrics in tests, and records
a private Record19 aggregate benchmark without committing transcript-derived
runtime outputs.
Task `13` publishes the commit safety gate and five-role subagent review prompts
(under `.cursor/skills/tara-review/review_process/`) for repeatable reviews before
each merge to `master`.

### Cursor CLI pipeline probe (benchmarks)

To validate the full stack through Cursor CLI `agent -p` without changing
deterministic specialists yet, enable the optional probe: set
`analysis.llm.cursor_cli_probe` to `true` with `analysis.llm.backend` set to
`cursor_cli`, or export `TARA_CURSOR_CLI_PROBE=1` for a one-off run. See
`.cursor/skills/tara-review/review_process/benchmarks/task-cursor-cli-pipeline-probe.md`. Automated tests mock
the CLI; set `TARA_CURSOR_CLI_E2E=1` to opt into a single real `pytest` smoke that
calls the actual Cursor binary.

CLI shortcuts: `--analysis-backend cursor_cli` with `--cursor-cli-probe` forces
the probe without editing JSON. `--context FILE` attaches general campaign
context to LLM analysis prompts, and `--prior-context FILE` attaches
previous-session context to the summary composer. Both accept `.md` or `.txt`;
relative config paths are resolved from the config file directory.

### Scene-driven blackboard enrichment

Scene enrichment is configured under `analysis.scenes` and is enabled by
default. It only performs LLM work when the analysis backend is `api` or
`cursor_cli`; deterministic runs fall back to the blackboard-only path and log a
non-fatal warning.

The scene flow writes private runtime artifacts beside `merged_transcription.json`:

- `scene_analysis.json`: ordered scene boundaries and short summaries.
- `scenes/scene_001.json`: one transcript slice per scene, including speaker
  metadata and original segment ids.
- `scene_descriptions.json`: long scene descriptions, key actions, state
  changes, continuity impacts, and scene facts.

Scene facts are converted into `EvidenceAnswer` rows before the regular
specialist agents run. Their answer ids include the specialist type and scene id,
for example `chronology_scene_003_00`, and their metadata records
`source: scene_description`, `scene_id`, title, and timestamps. The final
composer receives the full scene timeline plus blackboard facts so it can use
the scenes as the narrative backbone without copying raw transcript prose.

For a high-quality Cursor CLI run:

```powershell
python -m tara --merged-transcription "C:\path\to\merged_transcription.json" --analysis-backend cursor_cli --cursor-cli-probe --context "C:\path\to\campaign_context.md" --prior-context "C:\path\to\previous_sessions.md"
```

## Running

Analyze an existing merged transcription:

```powershell
conda activate DM
$env:PYTHONPATH = "src"
python -m tara --merged-transcription "C:\path\to\merged_transcription.json"
```

Add optional user context for LLM-assisted runs:

```powershell
python -m tara --merged-transcription "C:\path\to\merged_transcription.json" --context "C:\path\to\campaign_context.md" --prior-context "C:\path\to\previous_sessions.md"
```

The context files are freeform private text. Tara uses the current transcript as
the source of truth for session events, while context can guide names, aliases,
players, characters, and continuity. Context content is not copied to normal
summary artifacts; `--write-context-debug` writes a redacted debug copy under the
analysis output directory when explicitly requested.

Successful CLI runs print a JSON `TaraRunResult` to stdout with the generated
artifact paths.

Run from an audio directory with the Windows helper, which starts the local
transcription inference server shipped in this repo (`src/inference_server/`):

```bat
run_tara.bat --audio-dir "C:\path\to\audio"
```

The inference server log is written to `inference_server.log` in the TaraRepo
root by default. Use `--inference-log "C:\path\to\inference.log"` to override it.

For transcription and merge only, skip the analysis stage:

```bat
run_tara.bat --audio-dir "C:\path\to\audio" --transcription-only
```

Processing merges per-speaker transcription files into `merged_transcription.json`.
When source files are named like `1-willygorn.json`, each output segment records
speaker metadata such as `author.speaker = "willygorn"` and keeps the source file
name for traceability. Existing merged files without author metadata remain valid.

Install optional ASR dependencies when you need real transcription (not required
for analysis-only runs on `merged_transcription.json`):

```powershell
pip install -e ".[inference]"
```

Run the API server:

```powershell
conda activate DM
$env:PYTHONPATH = "src"
python -m uvicorn tara.server:app --host 127.0.0.1 --port 8080
```

The API exposes `GET /health`, `POST /v1/runs`, and `POST /v1/analysis`.
It is localhost-oriented by default. Set `TARA_API_TOKEN` and send
`Authorization: Bearer <token>` before exposing it beyond the local machine;
non-local requests are rejected when no token is configured.

`.env` is loaded from the TaraRepo project root, not from arbitrary process
working directories. Generated summaries and analysis artifacts contain
transcript-derived private content and should stay out of version control.
