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
