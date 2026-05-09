# TaraRepo

TaraRepo is the standalone implementation workspace for the new Tara analysis
pipeline described in `ARCHITECTURE.md`.

The project replaces the old scene-based analysis flow with a blackboard,
local retrieval, arbitration, and adversarial audit architecture. The old
`Tara` project remains the source of reference contracts for transcription,
processing, configuration patterns, logging, telemetry, usage reporting, and
local runner behavior.

## Migration Guardrails

- Do not modify the existing `../Tara`
  transcription, inference server, or processing code as part of this refactor.
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
paths lives in `review_process/review_agents.md`. Copy the report layout from
`review_process/report_template.md` and archive outputs under
`review_process/reviews/task-<number>-<short-slug>/`. See `review_process/README.md`
for a short index. Before every commit, run `git status --short`, stage explicit
paths, inspect `git diff --cached`, and refuse anything that looks like secrets,
`.env`, private media, or runtime analysis outputs.

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
This is the first deterministic implementation; LLM-assisted specialist and
composer behavior will be added behind the same typed boundaries later.
Task `11` wires the standalone CLI, JSON configuration, `.env` loading,
transcription-server integration, processing, FastAPI endpoints, and final
`session_summary.md` / `session_summary.json` generation.
Task `12` adds acceptance metrics, validates those metrics in tests, and records
a private Record19 aggregate benchmark without committing transcript-derived
runtime outputs.
Task `13` publishes the commit safety gate and five-role subagent review prompts
under `review_process/` for repeatable reviews before each merge to `master`.

### Cursor CLI pipeline probe (benchmarks)

To validate the full stack through Cursor CLI `agent -p` without changing
deterministic specialists yet, enable the optional probe: set
`analysis.llm.cursor_cli_probe` to `true` with `analysis.llm.backend` set to
`cursor_cli`, or export `TARA_CURSOR_CLI_PROBE=1` for a one-off run. See
`review_process/benchmarks/task-cursor-cli-pipeline-probe.md`. Automated tests mock
the CLI; set `TARA_CURSOR_CLI_E2E=1` to opt into a single real `pytest` smoke that
calls the actual Cursor binary.

CLI shortcuts: `--analysis-backend cursor_cli` with `--cursor-cli-probe` forces
the probe without editing JSON; `--prior-context FILE` attaches prior-session
markdown to the probe stdin only (deterministic analysis still uses the merged
transcription JSON on disk).

## Running

Analyze an existing merged transcription:

```powershell
conda activate DM
$env:PYTHONPATH = "src"
python -m tara --merged-transcription "C:\path\to\merged_transcription.json"
```

Successful CLI runs print a JSON `TaraRunResult` to stdout with the generated
artifact paths.

Run from an audio directory with the Windows helper, which starts the local
transcription inference server from the old `Tara` reference project:

```bat
run_tara.bat --audio-dir "C:\path\to\audio"
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
