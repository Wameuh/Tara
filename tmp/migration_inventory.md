# Migration Inventory

## Purpose

This inventory records the contracts and guardrails for the TaraRepo
blackboard-analysis refactor. It supports task `00` and should be updated only
when migration boundaries or preserved contracts change.

## Implementation Root

- New implementation root: `%USERPROFILE%\Documents\Projets\DM_Assistant\TaraRepo`
- Existing Tara reference root: `%USERPROFILE%\Documents\Projets\DM_Assistant\Tara`

The existing Tara project is a reference source only during this refactor. The
new analysis application is implemented in TaraRepo.

## Preserved Contracts

- Transcription is served by the existing inference-server workflow.
- Processing produces `merged_transcription.json`.
- `merged_transcription.json` is the canonical input to the new analysis
  pipeline.
- Final analysis outputs remain `session_summary.json` and `session_summary.md`.
- Usage and cost reporting should preserve the old telemetry intent: capture
  model, backend, token usage when available, and estimated cost from config
  pricing.

## Current Merged Transcription Shape

`Record19` confirms the current merged artifact has this high-level shape:

```json
{
  "text": "...",
  "segments": [
    {
      "start": 4.48,
      "end": 5.28,
      "text": "On est parti."
    }
  ],
  "language": "fr",
  "duration": 0.0,
  "model": "..."
}
```

The first formal model should validate `text` and `segments`, tolerate optional
metadata such as `language`, `duration`, and `model`, and preserve unknown
metadata for forward compatibility.

## Current Artifact Inventory

Reference location:
`%USERPROFILE%\Documents\Projets\DM_Assistant\Record_session\Record19\transcriptions`

The following artifacts exist in the current Tara workflow and are used only as
private reference material:

| Artifact | Current role | TaraRepo migration decision |
| --- | --- | --- |
| `merged_transcription.json` | Canonical merged processing output | Preserved as the only canonical analysis input |
| `scene_analysis.json` | Old scene analysis output | Replaced by `analysis_plan.json` and blackboard state |
| `scene_descriptions.json` | Old long scene-description artifact | Replaced by short sourced `EvidenceAnswer` objects and blackboard facts |
| `scenes/scene_*.json` | Old scene split outputs | Not central to the new architecture; may be reference-only during manual comparison |
| `session_summary.json` | Final structured summary | Preserved as a final output name, with a new schema focused on traceability and cost/call metadata |
| `session_summary.md` | Final human-readable summary | Preserved as the primary user-facing output |
| `verification_report.json` | Old verifier report | Replaced by `audit_report.json`, `arbitration_report.json`, and summary trace metadata |
| `session_summary_verified.json` | Old verifier-corrected JSON | Replaced by audited `session_summary.json` |
| `session_summary_verified.md` | Old verifier-corrected Markdown | Replaced by audited `session_summary.md` |

These files must not be committed from private session folders unless they are
explicitly sanitized or whitelisted in a later task.

## Current Local Runner Behavior

The existing `Tara\run_tara.bat` is the compatibility reference for local
developer workflow:

- parse `--audio-dir`, `--config`, `--previous-express-summaries`,
  `--server-host`, and `--server-port`;
- start `uvicorn inference_server.app:app` locally;
- poll `http://localhost:<port>/health`;
- run the Tara app against the audio directory;
- attempt cleanup of the inference-server process it started.

TaraRepo should provide an equivalent local runner while also supporting a
server deployment with an API endpoint for transcription/full-run orchestration.
The new runner should improve process ownership by cleaning up only a process it
started or by reusing an already-running server without stopping it.

## Reusable Tara References

Safe reference candidates from the existing Tara project:

- `src/tara/processing/models.py`: segment and processing result contracts.
- `src/tara/processing/agent.py`: merge output shape and deduplication intent.
- `src/tara/configuration/models.py`: JSON configuration style.
- `src/tara/usage_reporting/service.py`: token aggregation and cost estimation
  intent.
- `run_tara.bat`: local inference-server startup and health-check workflow.

Do not copy old scene-analysis implementation as-is. It is replaced by the
blackboard analysis architecture.

## Runtime And Privacy Guardrails

The following must not be committed by default:

- `.env`, `.env.*`, API keys, tokens, credentials, OAuth files;
- private audio files;
- private raw transcription outputs;
- `merged_transcription.json` and generated summary artifacts;
- logs, caches, local SQLite databases, and runtime debug outputs;
- raw LLM prompts/responses when they contain transcript content.

Task commits must stage paths explicitly and verify staged status, staged file
names, and staged diffs before committing.

## Checkpoint For Task 00

- Current branch: initial TaraRepo branch.
- Checkpoint policy: the task `00` commit is the migration checkpoint before
  implementation begins. The commit hash is intentionally not embedded in this
  source file because it is created by committing this checkpoint.
- Reference evidence captured on 2026-05-09:
  - `Record19` contains the current merged transcription and old analysis
    outputs listed above.
  - `Tara\run_tara.bat` documents the current local inference-server startup and
    application-run workflow.
  - `Tara\config\configuration.json` documents the current JSON configuration
    shape, transcription endpoint, processing output name, and usage pricing
    tables.

## Protected Test Inventory

The old Tara repository remains the reference for non-analysis regression
coverage. These paths identify the protected suites to preserve or mirror when
TaraRepo gains its own executable test setup:

- `Tara\tests\tara\transcription\`
- `Tara\tests\inference_server\`
- `Tara\tests\integration\test_parakeet_server.py`
- `Tara\tests\inference_server\test_integration.py`
- `Tara\tests\tara\processing\`
- `Tara\tests\tara\configuration\`
- `Tara\tests\tara\logging\`
- `Tara\tests\tara\telemetry\`
- `Tara\tests\tara\usage_reporting\`
- `Tara\tests\tara\control\test_agent_transcription.py`
- `Tara\tests\tara\control\test_agent_usage_reporting.py`

Old scene-analysis tests are not protected as exact behavior. They can be mined
for useful fixtures or expectations, but the scene-based behavior is replaced by
the blackboard architecture.
