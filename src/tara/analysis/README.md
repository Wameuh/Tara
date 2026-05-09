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
