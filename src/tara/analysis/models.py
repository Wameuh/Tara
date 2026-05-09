"""Core data models for the Tara blackboard analysis pipeline."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

JsonObject = dict[str, JsonValue]


class FactStatus(StrEnum):
    """Status assigned to evidence-derived facts."""

    SUPPORTED = "supported"
    PARTIAL = "partial"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


class Confidence(StrEnum):
    """Confidence level for agent answers and blackboard facts."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ConflictSeverity(StrEnum):
    """Severity of a contradiction or audit issue."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class ClaimType(StrEnum):
    """Domain category for a claim in the analysis pipeline."""

    CHRONOLOGY = "chronology"
    COMBAT_OUTCOME = "combat_outcome"
    CHARACTER_STATE = "character_state"
    QUEST_CONTINUITY = "quest_continuity"
    RESOURCE_STATE = "resource_state"
    FINAL_STATE = "final_state"


class TaraModel(BaseModel):
    """Base model with stable JSON serialization helpers."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the model to a JSON-compatible dictionary.

        Returns:
            Dictionary representation using enum values.
        """
        return self.model_dump(mode="json")

    def to_json(self, path: Path) -> None:
        """Write the model to a JSON file.

        Args:
            path: Destination file path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a model instance from a dictionary.

        Args:
            data: Raw model data.

        Returns:
            Validated model instance.
        """
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, path: Path) -> Self:
        """Load a model instance from a JSON file.

        Args:
            path: Source JSON file.

        Returns:
            Validated model instance.
        """
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class TranscriptionSegment(TaraModel):
    """Segment from `merged_transcription.json`.

    Attributes:
        start: Segment start time in seconds.
        end: Segment end time in seconds.
        text: Segment text.
    """

    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        """Validate that the segment does not end before it starts."""
        if self.end < self.start:
            raise ValueError("Segment end must be greater than or equal to start.")
        return self


class MergedTranscription(TaraModel):
    """Canonical analysis input produced by processing."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    text: str
    segments: list[TranscriptionSegment]
    language: str | None = None
    duration: float | None = Field(default=None, ge=0.0)
    model: str | None = None


class EvidenceSupport(TaraModel):
    """Raw evidence reference supporting a claim."""

    chunk_id: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    segment_ids: list[int] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        """Validate that the support interval is well ordered."""
        if self.end < self.start:
            raise ValueError("Evidence support end must be greater than start.")
        return self


class EvidenceChunk(TaraModel):
    """Chunk indexed for retrieval."""

    chunk_id: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str
    segment_ids: list[int]
    detected_entities: list[str] = Field(default_factory=list)
    lexical_tags: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_chunk(self) -> Self:
        """Validate required chunk invariants."""
        if self.end < self.start:
            raise ValueError("Evidence chunk end must be greater than start.")
        if not self.segment_ids:
            raise ValueError("Evidence chunk must reference at least one segment.")
        return self


class RetrievalQuery(TaraModel):
    """Query sent to the local evidence retriever."""

    query_id: str
    text: str
    question_id: str | None = None
    limit: int = Field(default=5, ge=1)
    filters: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)


class RetrievedEvidence(TaraModel):
    """Evidence chunk returned by retrieval."""

    query_id: str
    chunk: EvidenceChunk
    score: float = Field(ge=0.0)
    source: str = "local"
    metadata: JsonObject = Field(default_factory=dict)


class AnalysisQuestion(TaraModel):
    """Question assigned to a specialist agent."""

    question_id: str
    priority: int = Field(ge=1, le=5)
    retrieval_queries: list[RetrievalQuery]
    required_output_schema: JsonObject = Field(default_factory=dict)
    risk_level: ConflictSeverity = ConflictSeverity.MINOR
    responsible_agent: str
    metadata: JsonObject = Field(default_factory=dict)


class AnalysisPlan(TaraModel):
    """Collection of analysis questions for one run."""

    plan_id: str = "analysis_plan_v1"
    questions: list[AnalysisQuestion]
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_questions(self) -> Self:
        """Validate that the plan contains unique question identifiers."""
        question_ids = [question.question_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Analysis question IDs must be unique.")
        return self


class EvidenceAnswer(TaraModel):
    """Short sourced answer produced by a specialist agent."""

    answer_id: str
    question_id: str
    claim: str
    status: FactStatus
    importance: int = Field(ge=1, le=5)
    support: list[EvidenceSupport] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    confidence: Confidence
    claim_type: ClaimType | None = None
    is_critical: bool = False
    notes: str | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_answer_invariants(self) -> Self:
        """Validate support and critical-claim invariants."""
        if self.status == FactStatus.SUPPORTED and not self.support:
            raise ValueError("Supported evidence answers must have support.")
        if (self.is_critical or self.importance >= 4) and self.claim_type is None:
            raise ValueError("Critical evidence answers must define claim_type.")
        return self


class BlackboardFact(TaraModel):
    """Fact accepted into the shared blackboard."""

    fact_id: str
    answer_id: str | None = None
    claim: str
    status: FactStatus
    confidence: Confidence
    importance: int = Field(ge=1, le=5)
    claim_type: ClaimType | None = None
    support: list[EvidenceSupport] = Field(default_factory=list)
    is_critical: bool = False
    is_final_state: bool = False
    final_timestamp: float | None = Field(default=None, ge=0.0)
    do_not_claim: bool = False
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fact_invariants(self) -> Self:
        """Validate support, final state, and critical-claim invariants."""
        if self.status == FactStatus.SUPPORTED and not self.support:
            raise ValueError("Supported blackboard facts must have support.")
        if self.is_final_state and self.final_timestamp is None:
            raise ValueError("Final-state facts must have a final timestamp.")
        if (self.is_critical or self.importance >= 4) and self.claim_type is None:
            raise ValueError("Critical blackboard facts must define claim_type.")
        return self


class Conflict(TaraModel):
    """Conflict detected between blackboard facts or evidence answers."""

    conflict_id: str
    answer_ids: list[str]
    severity: ConflictSeverity
    description: str
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_conflict(self) -> Self:
        """Validate that a conflict compares at least two claims."""
        if len(self.answer_ids) < 2:
            raise ValueError("A conflict must reference at least two answers.")
        return self


class ArbitrationDecision(TaraModel):
    """Decision produced by the arbitration panel."""

    conflict_id: str
    decision: Literal["accept_claim", "reject_all", "merge_claims", "claim_forbidden"]
    accepted_answer_id: str | None = None
    rejected_answer_ids: list[str] = Field(default_factory=list)
    basis: str
    required_summary_policy: Literal[
        "claim_allowed",
        "claim_forbidden",
        "mark_unconfirmed",
    ]
    metadata: JsonObject = Field(default_factory=dict)


class SummarySection(TaraModel):
    """A section in the generated summary."""

    section_id: str
    title: str
    content: str
    supporting_answer_ids: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class SummaryDraft(TaraModel):
    """Draft summary produced before adversarial audit."""

    markdown: str
    sections: list[SummarySection]
    forbidden_claim_ids: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_draft_support(self) -> Self:
        """Validate that draft sections are traceable to answers."""
        unsupported = [
            section.section_id
            for section in self.sections
            if not section.supporting_answer_ids
        ]
        if unsupported:
            raise ValueError(
                "Summary draft sections must define supporting_answer_ids: "
                + ", ".join(unsupported)
            )
        return self


class AuditFinding(TaraModel):
    """Issue reported by the adversarial audit agent."""

    finding_id: str
    severity: ConflictSeverity
    claim: str
    issue: str
    required_action: Literal[
        "remove",
        "rewrite",
        "mark_unconfirmed",
        "replan",
        "retrieve_more",
    ]
    related_answer_ids: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class FinalSummary(TaraModel):
    """Audited final summary artifact."""

    markdown: str
    sections: list[SummarySection]
    findings: list[AuditFinding] = Field(default_factory=list)
    llm_call_count: int = Field(default=0, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_summary_support(self) -> Self:
        """Validate that final summary sections are traceable to answers."""
        unsupported = [
            section.section_id
            for section in self.sections
            if not section.supporting_answer_ids
        ]
        if unsupported:
            raise ValueError(
                "Final summary sections must define supporting_answer_ids: "
                + ", ".join(unsupported)
            )
        return self
