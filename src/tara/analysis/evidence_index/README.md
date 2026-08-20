# Evidence Index

The evidence index builds local CPU-only chunks from `merged_transcription.yaml`
so downstream LLM agents can retrieve focused evidence instead of reading a full
session transcript.

The current implementation is intentionally lightweight:

- sliding time-window chunks with overlap;
- segment IDs and timestamps preserved on each `EvidenceChunk`;
- simple capitalized-entity detection and coarse keyword tags;
- lexical retrieval based on normalized token overlap;
- YAML multi-document chunk export and YAML metadata export.

Exports include transcript text and must remain private runtime artifacts.

Operational bounds are enforced by default to reduce accidental resource spikes:
maximum segment count, maximum segment text length, maximum chunk count, and
maximum query token count. Callers can lower these limits for hosted use cases.

No external embedding API or LLM call is used. Future tasks can add local vector
scoring as a secondary ranker, but lexical retrieval remains the mandatory
fallback.

The in-memory index can be reconstructed from `evidence_chunks.yaml` and
`evidence_index_metadata.yaml` with `EvidenceIndex.from_artifacts`; this is the
current local equivalent to a persisted SQLite index.

Legacy `.json` / `.jsonl` artifacts remain readable via `load_yaml_or_json()` at
call sites that still ingest older runs.
