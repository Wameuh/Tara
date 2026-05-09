# 00 - Inventory and migration guardrails

## Objective

Prepare the refactor without breaking the working parts. This task freezes the
scope: transcription is not modified, and the analysis layer is replaced in
TaraRepo.

## Tara reference contracts

- `src/tara/transcription/`
- `src/inference_server/`
- `src/tara/processing/`
- `src/tara/control/agent.py`
- `src/tara/configuration/`
- `src/tara/cli/`
- `src/tara/logging/`
- `src/tara/telemetry/`
- `src/tara/usage_reporting/`
- existing transcription, inference server, and processing tests
- real private examples in `Record_session/Record19/transcriptions/`, used only
  as external reference material

## Do not reuse as-is

- agentic logic from `scene_analyzer`, `scene_descriptor`, `scene_summarizer`,
  and `scene_verifier`;
- current long-description prompts;
- late exhaustive claim-by-claim verification;
- structural dependency on scene splitting.

## Tasks

1. Inventory the current outputs:
   - `merged_transcription.json`
   - `scene_analysis.json`
   - `scene_descriptions.json`
   - `session_summary.json`
   - `verification_report.json`
2. Identify the contracts to preserve:
   - analysis input: `merged_transcription.json`;
   - expected final output: `session_summary.json` + `session_summary.md`;
   - telemetry usage;
   - CLI configuration.
3. Create a migration branch or checkpoint.
4. Record the tests that must not be affected:
   - transcription;
   - inference server;
   - processing.
5. Create a reference set with `Record19`.

## Validation Criteria

- The "transcription untouched" boundary is documented.
- Replaced modules are listed.
- A reference run or reference artifacts exist for comparison.
- Non-analysis tests are identified.

## Implementation Status

Status: Completed

Notes:

- The migration boundary is documented in `README.md` and
  `tmp/migration_inventory.md`.
- The current artifact inventory covers `merged_transcription.json`,
  `scene_analysis.json`, `scene_descriptions.json`, `session_summary.json`,
  `session_summary.md`, `verification_report.json`, and verified summary
  variants.
- `Record19` is identified as the manual evaluation reference without copying
  private artifacts into the repository.
- Private artifacts, secrets, logs, caches, local databases, and runtime outputs
  are excluded by `.gitignore`.
- The current `Tara\run_tara.bat` behavior is captured as the compatibility
  reference for the future TaraRepo local runner.
- The task `00` commit is the migration checkpoint before implementation begins.
