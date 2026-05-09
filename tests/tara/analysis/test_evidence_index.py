"""Tests for local evidence chunking and retrieval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tara.analysis.evidence_index import EvidenceIndex
from tara.analysis.models import MergedTranscription, RetrievalQuery


def _transcription() -> MergedTranscription:
    """Create a synthetic merged transcription for retrieval tests."""
    return MergedTranscription.model_validate(
        {
            "text": (
                "Molnir tombe dans le sanctuaire. "
                "La lance brille. "
                "Une potion soigne Aelia. "
                "Le groupe fuit le temple."
            ),
            "segments": [
                {
                    "start": 0.0,
                    "end": 20.0,
                    "text": "Molnir est mort dans le sanctuaire.",
                },
                {"start": 20.0, "end": 40.0, "text": "La lance brille."},
                {"start": 40.0, "end": 60.0, "text": "Une potion soigne Aelia."},
                {"start": 60.0, "end": 80.0, "text": "Le groupe fuit le temple."},
            ],
            "language": "fr",
            "duration": 80.0,
        }
    )


def test_index_builds_overlapping_chunks_with_segment_ids() -> None:
    """Chunking should preserve timestamps and original segment IDs."""
    index = EvidenceIndex.from_transcription(
        _transcription(),
        target_window_seconds=40.0,
        overlap_seconds=10.0,
    )

    assert len(index.chunks) == 3
    assert index.chunks[0].segment_ids == [0, 1]
    assert index.chunks[1].segment_ids == [1, 2, 3]
    assert index.chunks[0].start == 0.0
    assert index.chunks[-1].end == 80.0
    assert index.coverage.indexed_segment_count == 4
    assert index.coverage.uncovered_ranges == []


def test_index_rejects_invalid_chunking_configuration() -> None:
    """Invalid overlap/window combinations should fail early."""
    with pytest.raises(ValueError, match="smaller"):
        EvidenceIndex.from_transcription(
            _transcription(),
            target_window_seconds=30.0,
            overlap_seconds=30.0,
        )


def test_retrieval_finds_molnir_death_context() -> None:
    """Lexical retrieval should find simple direct query terms."""
    index = EvidenceIndex.from_transcription(_transcription(), 40.0, 10.0)

    results = index.retrieve(
        RetrievalQuery(query_id="q001", text="Molnir mort", limit=2)
    )

    assert results
    assert "Molnir est mort" in results[0].chunk.text
    assert results[0].source == "lexical"


def test_retrieval_handles_accent_insensitive_queries() -> None:
    """Query normalization should match accented and unaccented terms."""
    index = EvidenceIndex.from_transcription(_transcription(), 40.0, 10.0)

    results = index.retrieve(RetrievalQuery(query_id="q002", text="sanctuaire"))

    assert results
    assert "sanctuaire" in results[0].chunk.text


def test_retrieval_finds_lance_and_potion_examples() -> None:
    """Task 04 example terms should retrieve focused evidence."""
    index = EvidenceIndex.from_transcription(_transcription(), 40.0, 10.0)

    lance = index.retrieve(RetrievalQuery(query_id="q_lance", text="lance", limit=1))
    potion = index.retrieve(RetrievalQuery(query_id="q_potion", text="potion", limit=1))

    assert lance and "lance" in lance[0].chunk.text
    assert potion and "potion" in potion[0].chunk.text


def test_retrieval_limit_override_and_matched_tokens() -> None:
    """The method-level limit should override query limit and trace matches."""
    index = EvidenceIndex.from_transcription(_transcription(), 40.0, 10.0)

    results = index.retrieve(
        RetrievalQuery(query_id="q_limit", text="Molnir dragon", limit=5),
        limit=1,
    )

    assert len(results) == 1
    assert results[0].metadata["matched_tokens"] == ["molnir"]


def test_retrieval_applies_filters() -> None:
    """Retrieval filters should narrow chunks before scoring."""
    index = EvidenceIndex.from_transcription(_transcription(), 40.0, 10.0)

    results = index.retrieve(
        RetrievalQuery(
            query_id="q_filter",
            text="Molnir potion",
            filters={"lexical_tags": ["healing"]},
        )
    )

    assert results
    assert all("healing" in result.chunk.lexical_tags for result in results)
    assert "potion" in results[0].chunk.text


def test_retrieval_rejects_oversized_queries() -> None:
    """Pathological queries should be rejected before scoring all chunks."""
    index = EvidenceIndex.from_transcription(_transcription(), 40.0, 10.0)

    with pytest.raises(ValueError, match="maximum token count"):
        index.retrieve(
            RetrievalQuery(
                query_id="q_big",
                text=" ".join(f"token{i}" for i in range(10)),
            ),
            max_query_tokens=5,
        )


def test_retrieval_returns_empty_for_unmatched_query() -> None:
    """Unmatched lexical queries should return no evidence."""
    index = EvidenceIndex.from_transcription(_transcription(), 40.0, 10.0)

    assert index.retrieve(RetrievalQuery(query_id="q003", text="dragon")) == []


def test_keyword_tags_and_entities_are_added() -> None:
    """Chunks should carry lightweight metadata for routing and filtering."""
    index = EvidenceIndex.from_transcription(_transcription(), 40.0, 10.0)
    first_chunk = index.chunks[0]

    assert "Molnir" in first_chunk.detected_entities
    assert "combat" in first_chunk.lexical_tags
    assert "place" in first_chunk.lexical_tags
    assert first_chunk.metadata["temporal_position"] == 0


def test_json_exports_write_chunks_and_metadata(tmp_path: Path) -> None:
    """Chunk and metadata exports should produce JSON artifacts."""
    index = EvidenceIndex.from_transcription(_transcription(), 40.0, 10.0)
    chunks_path = tmp_path / "evidence_chunks.jsonl"
    metadata_path = tmp_path / "evidence_index_metadata.json"

    index.write_chunks_jsonl(chunks_path)
    index.write_metadata_json(metadata_path)
    loaded = EvidenceIndex.from_artifacts(chunks_path, metadata_path)

    rows = [
        json.loads(line)
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
    ]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert rows[0]["chunk_id"] == "chunk_0000"
    assert rows[0]["segment_ids"] == [0, 1]
    assert rows[0]["start"] == 0.0
    assert "window_start" in rows[0]["metadata"]
    assert metadata["chunk_count"] == len(index.chunks)
    assert metadata["target_window_seconds"] == 40.0
    assert metadata["coverage"]["segment_count"] == 4
    assert loaded.retrieve(RetrievalQuery(query_id="q_loaded", text="Molnir"))


def test_coverage_reports_empty_text_segments() -> None:
    """Coverage report should flag segments with no indexable text."""
    transcription = MergedTranscription.model_validate(
        {
            "text": "Molnir parle.",
            "segments": [
                {"start": 0.0, "end": 10.0, "text": ""},
                {"start": 10.0, "end": 20.0, "text": "Molnir parle."},
            ],
            "duration": 20.0,
        }
    )

    index = EvidenceIndex.from_transcription(transcription, 20.0, 0.0)

    assert index.coverage.empty_text_segment_ids == [0]
    assert index.coverage.indexed_segment_count == 2


def test_coverage_reports_uncovered_ranges() -> None:
    """Coverage report should expose gaps between indexed chunks."""
    transcription = MergedTranscription.model_validate(
        {
            "text": "Début. Fin.",
            "segments": [
                {"start": 0.0, "end": 10.0, "text": "Début."},
                {"start": 100.0, "end": 110.0, "text": "Fin."},
            ],
            "duration": 110.0,
        }
    )

    index = EvidenceIndex.from_transcription(transcription, 20.0, 0.0)

    assert index.coverage.uncovered_ranges == [(10.0, 100.0)]
    assert index.coverage.covered_duration_seconds == 20.0


def test_index_rejects_missing_segments_for_nonempty_text() -> None:
    """Chunking should not silently ignore non-empty transcript text."""
    transcription = MergedTranscription(text="Only text.", segments=[])

    with pytest.raises(ValueError, match="requires source segments"):
        EvidenceIndex.from_transcription(transcription)


def test_index_rejects_operational_bound_violations() -> None:
    """Index builds should enforce configured resource bounds."""
    with pytest.raises(ValueError, match="maximum segment count"):
        EvidenceIndex.from_transcription(_transcription(), max_segments=1)
