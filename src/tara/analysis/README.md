# Tara Analysis Package

This package contains the new blackboard-based Tara analysis pipeline.

## LLM Runner

`llm_runner.py` defines the shared LLM execution layer used by future agents.
Agents submit `LLMRequest` objects and receive `LLMResponse` objects without
depending on OpenAI or Cursor CLI directly.

Supported backends:

- `api`: OpenAI-compatible Responses API backend, implemented first.
- `cursor_cli`: Cursor CLI backend using `agent -p`, implemented behind the same
  interface and tested with mocked subprocess execution.

The `api` backend requires an explicit model in the request or runner
configuration. The `cursor_cli` backend reports `Auto` when neither request nor
configuration provides a model.

The runner records purpose, backend, model, token usage, estimated cost, retry
attempt, and allow-listed request metadata when a telemetry recorder is
provided. Metadata is dropped from telemetry unless its key is explicitly listed
in `LLMRunnerConfig.telemetry_metadata_keys`.

Cursor CLI prompts are sent through stdin by default to avoid exposing transcript
content in process arguments. The subprocess environment is allow-listed so API
keys and unrelated secrets are not inherited by default. Argument-based prompt
transport remains available through `cursor_prompt_transport="argv"` for
compatibility testing.

The standalone pipeline can run an optional **Cursor CLI probe** (one small
non-transcript completion) before deterministic analysis when
`analysis.llm.cursor_cli_probe` is true or `TARA_CURSOR_CLI_PROBE` is set, so
benchmarks can record real `agent -p` usage without wiring LLM into every
specialist yet.

## Core Models

`models.py` defines the stable Pydantic schemas exchanged by future analysis
agents:

- canonical `MergedTranscription` input;
- retrieval objects such as `EvidenceChunk`, `RetrievalQuery`, and
  `RetrievedEvidence`;
- planning objects such as `AnalysisQuestion` and `AnalysisPlan`;
- sourced facts such as `EvidenceAnswer` and `BlackboardFact`;
- conflict, arbitration, summary, audit, and final-summary artifacts.

Critical invariants are enforced at model validation time: supported facts need
raw support, final-state facts need a final timestamp, high-importance or
explicitly critical claims need a claim type, and summary draft/final sections
need `supporting_answer_ids`.

Operationally, `EvidenceAnswer` and `BlackboardFact` require `claim_type` when
`is_critical` is `true` or `importance >= 4` on the 1-5 importance scale.
`ClaimType` values are `chronology`, `combat_outcome`, `character_state`,
`quest_continuity`, `resource_state`, and `final_state`.

Conflicts must reference at least two answer IDs so arbitration always compares
multiple claims.

These models carry sensitive data. `MergedTranscription` stores full transcript
text and allows unknown top-level fields from the processing contract for
forward compatibility, so serialized instances must be treated as private
runtime artifacts. Extensible `metadata`, `filters`, and output-schema fields are
restricted to JSON-compatible values to keep artifacts serializable and reduce
accidental object leakage.

## Evidence Index

`evidence_index/` builds a local CPU-only in-memory index from
`MergedTranscription`. It creates overlapping time-window `EvidenceChunk`
objects, preserves segment IDs and timestamps, detects lightweight keyword tags
and capitalized entities, and retrieves chunks with lexical token-overlap
scoring.

The index can export `evidence_chunks.jsonl` and
`evidence_index_metadata.json` for debugging or traceability, then reconstruct
the in-memory index from those artifacts. These files may contain transcript
text and remain private runtime artifacts by default.

Chunking is segment-driven: `MergedTranscription.segments` is the source of
indexed text and timestamps. `MergedTranscription.text` is treated as aggregate
metadata and is not split independently.

## Agent Pipeline

`agents.py` provides the deterministic first implementation of the blackboard
pipeline:

- `AnalysisPlannerAgent` creates standard questions for chronology, combat
  outcomes, character state, quest continuity, resources, and uncertainty.
- Specialist agents answer from retrieved chunks only and emit sourced
  `EvidenceAnswer` objects, falling back to uncertain answers when retrieval is
  empty.
- `BlackboardController` validates, deduplicates, classifies, preserves
  rejected/uncertain facts, and detects competing critical claims.
- `ArbitrationPanel` keeps unresolved conflicts out of silent composition.
- `SummaryComposerAgent` creates supported French summary draft sections.
- `AdversarialAuditAgent` checks unsupported references, forbidden-claim leaks,
  and empty drafts.
- `FinalPatchAgent` marks unresolved critical findings as non-confirmed without
  adding new facts.
- `AnalysisOrchestrator` runs the bounded audit/replanning loop with a default
  limit of three attempts.

This first implementation is deterministic and uses the shared retrieval/model
contracts. LLM-assisted behavior can be added behind the same typed boundaries.
Specialist classes already accept optional `llm_runner` and config values, but
the current CI-safe path does not invoke them. The application layer passes the
configured runner into that boundary so future LLM-assisted behavior does not
need to change specialist construction.

The standalone application layer in `tara.pipeline` builds this package from
`merged_transcription.json`, writes private evidence/debug artifacts, and emits
the user-facing `session_summary.md` plus traceable `session_summary.json`.
The JSON output also includes acceptance metrics from `tara.acceptance`, such as
support rate, forbidden-claim leaks, critical conflict leakage, and deterministic
LLM usage/cost counters.
