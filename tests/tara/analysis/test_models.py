"""Tests for Tara analysis data models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tara.analysis.models import (
    AnalysisPlan,
    AnalysisQuestion,
    AuditFinding,
    BlackboardFact,
    ClaimType,
    Confidence,
    Conflict,
    ConflictSeverity,
    EvidenceAnswer,
    EvidenceChunk,
    EvidenceSupport,
    FactStatus,
    FinalSummary,
    MergedTranscription,
    RetrievalQuery,
    SummaryDraft,
    SummarySection,
    TranscriptionSegment,
)


def _support() -> EvidenceSupport:
    """Create a reusable evidence support reference."""
    return EvidenceSupport(chunk_id="c001", start=1.0, end=2.0, segment_ids=[1])


def _chunk() -> EvidenceChunk:
    """Create a reusable evidence chunk."""
    return EvidenceChunk(
        chunk_id="c001",
        start=0.0,
        end=60.0,
        text="Molnir tombe au sol.",
        segment_ids=[1, 2],
        detected_entities=["Molnir"],
        lexical_tags=["combat"],
    )


def test_merged_transcription_allows_optional_metadata() -> None:
    """Merged transcription should validate known fields and preserve extras."""
    merged = MergedTranscription.model_validate(
        {
            "text": "On est parti.",
            "segments": [{"start": 0.0, "end": 1.0, "text": "On est parti."}],
            "language": "fr",
            "duration": 1.0,
            "model": "parakeet",
            "source_file": "example.json",
        }
    )

    assert merged.segments == [
        TranscriptionSegment(start=0.0, end=1.0, text="On est parti.")
    ]
    assert merged.model_extra == {"source_file": "example.json"}


def test_segment_rejects_invalid_time_order() -> None:
    """Segments should reject an end timestamp before the start timestamp."""
    with pytest.raises(ValidationError, match="greater than or equal"):
        TranscriptionSegment(start=2.0, end=1.0, text="bad")


def test_evidence_chunk_requires_segments() -> None:
    """Evidence chunks must trace back to source segments."""
    with pytest.raises(ValidationError, match="at least one segment"):
        EvidenceChunk(chunk_id="c001", start=0.0, end=1.0, text="x", segment_ids=[])


def test_supported_answer_requires_support() -> None:
    """Supported evidence answers must have raw evidence support."""
    with pytest.raises(ValidationError, match="must have support"):
        EvidenceAnswer(
            answer_id="a001",
            question_id="q001",
            claim="Molnir est mort.",
            status=FactStatus.SUPPORTED,
            importance=5,
            confidence=Confidence.HIGH,
            claim_type=ClaimType.COMBAT_OUTCOME,
        )


def test_critical_answer_requires_claim_type() -> None:
    """Critical evidence answers must declare their claim type."""
    with pytest.raises(ValidationError, match="must define claim_type"):
        EvidenceAnswer(
            answer_id="a001",
            question_id="q001",
            claim="Molnir est mort.",
            status=FactStatus.PARTIAL,
            importance=5,
            confidence=Confidence.MEDIUM,
            is_critical=True,
        )


def test_high_importance_answer_requires_claim_type() -> None:
    """High-importance answers should be treated as critical claims."""
    with pytest.raises(ValidationError, match="must define claim_type"):
        EvidenceAnswer(
            answer_id="a001",
            question_id="q001",
            claim="Molnir est mort.",
            status=FactStatus.PARTIAL,
            importance=5,
            confidence=Confidence.MEDIUM,
        )


def test_evidence_support_rejects_invalid_time_order() -> None:
    """Evidence support intervals should reject invalid time ordering."""
    with pytest.raises(ValidationError, match="greater than start"):
        EvidenceSupport(chunk_id="c001", start=10.0, end=5.0)


def test_blackboard_fact_requires_final_timestamp() -> None:
    """Final-state blackboard facts must include their latest timestamp."""
    with pytest.raises(ValidationError, match="Final-state"):
        BlackboardFact(
            fact_id="f001",
            claim="Le groupe sort du temple.",
            status=FactStatus.SUPPORTED,
            confidence=Confidence.HIGH,
            importance=5,
            claim_type=ClaimType.FINAL_STATE,
            support=[_support()],
            is_final_state=True,
        )


def test_supported_blackboard_fact_requires_support() -> None:
    """Supported blackboard facts must have support."""
    with pytest.raises(ValidationError, match="must have support"):
        BlackboardFact(
            fact_id="f001",
            claim="Le groupe sort du temple.",
            status=FactStatus.SUPPORTED,
            confidence=Confidence.HIGH,
            importance=3,
        )


def test_high_importance_blackboard_fact_requires_claim_type() -> None:
    """High-importance blackboard facts should be typed critical claims."""
    with pytest.raises(ValidationError, match="must define claim_type"):
        BlackboardFact(
            fact_id="f001",
            claim="Molnir est mort.",
            status=FactStatus.PARTIAL,
            confidence=Confidence.MEDIUM,
            importance=5,
        )


def test_blackboard_fact_accepts_supported_final_state() -> None:
    """A supported final-state fact should serialize with enum values."""
    fact = BlackboardFact(
        fact_id="f001",
        answer_id="a001",
        claim="Le groupe sort du temple.",
        status=FactStatus.SUPPORTED,
        confidence=Confidence.HIGH,
        importance=5,
        claim_type=ClaimType.FINAL_STATE,
        support=[_support()],
        is_final_state=True,
        final_timestamp=3600.0,
    )

    assert fact.to_dict()["status"] == "supported"
    assert fact.to_dict()["claim_type"] == "final_state"


def test_analysis_plan_requires_unique_question_ids() -> None:
    """Analysis plans should reject duplicate question identifiers."""
    query = RetrievalQuery(query_id="r001", text="Molnir")
    question = AnalysisQuestion(
        question_id="q001",
        priority=1,
        retrieval_queries=[query],
        responsible_agent="ChronologyAgent",
    )

    with pytest.raises(ValidationError, match="unique"):
        AnalysisPlan(questions=[question, question])


def test_conflict_requires_two_answers() -> None:
    """Conflicts should compare at least two answers."""
    with pytest.raises(ValidationError, match="at least two"):
        Conflict(
            conflict_id="conflict_001",
            answer_ids=["a001"],
            severity=ConflictSeverity.CRITICAL,
            description="Only one side.",
        )


def test_final_summary_sections_require_support() -> None:
    """Final summary sections must remain traceable to answer IDs."""
    with pytest.raises(ValidationError, match="supporting_answer_ids"):
        FinalSummary(
            markdown="## Résumé exécutif\nTexte.",
            sections=[
                SummarySection(
                    section_id="summary",
                    title="Résumé exécutif",
                    content="Texte.",
                )
            ],
        )


def test_summary_round_trip_json(tmp_path: Path) -> None:
    """Final summaries should round-trip through JSON files."""
    summary = FinalSummary(
        markdown="## Résumé exécutif\nMolnir tombe.",
        sections=[
            SummarySection(
                section_id="executive_summary",
                title="Résumé exécutif",
                content="Molnir tombe.",
                supporting_answer_ids=["combat_001"],
            )
        ],
        findings=[
            AuditFinding(
                finding_id="audit_001",
                severity=ConflictSeverity.MINOR,
                claim="Molnir tombe.",
                issue="Verified.",
                required_action="rewrite",
                related_answer_ids=["combat_001"],
            )
        ],
        llm_call_count=3,
        estimated_cost_usd=0.02,
    )
    path = tmp_path / "summary.json"

    summary.to_json(path)
    loaded = FinalSummary.from_json(path)

    assert loaded == summary


def test_summary_draft_serializes_for_composer_contract() -> None:
    """Summary drafts should serialize and round-trip composer contract fields."""
    draft = SummaryDraft(
        markdown="draft",
        sections=[
            SummarySection(
                section_id="points",
                title="Points clés",
                content="Point.",
                supporting_answer_ids=["a001"],
            )
        ],
        forbidden_claim_ids=["a999"],
        metadata={"template": "default"},
    )
    data = draft.to_dict()
    loaded = SummaryDraft.from_dict(data)

    assert data["forbidden_claim_ids"] == ["a999"]
    assert loaded == draft


def test_summary_draft_sections_require_support() -> None:
    """Summary drafts must keep section-to-answer traceability."""
    with pytest.raises(ValidationError, match="supporting_answer_ids"):
        SummaryDraft(
            markdown="draft",
            sections=[
                SummarySection(
                    section_id="points",
                    title="Points clés",
                    content="Point.",
                )
            ],
        )


def test_model_rejects_non_json_metadata() -> None:
    """Metadata extension fields should remain JSON-compatible."""
    with pytest.raises(ValidationError):
        RetrievalQuery(
            query_id="r001",
            text="Molnir",
            metadata={"unsafe": object()},
        )


def test_model_from_dict_helper() -> None:
    """Models should provide a consistent from_dict helper."""
    data: dict[str, Any] = _chunk().to_dict()

    assert EvidenceChunk.from_dict(data) == _chunk()
