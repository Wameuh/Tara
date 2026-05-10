"""Strict JSON parsing for agentic LLM responses."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError

from tara.analysis.models import ClaimType, TaraModel

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def extract_json_text(raw: str) -> str:
    """Strip markdown fences and return JSON text for parsing.

    Args:
        raw: Raw LLM completion text.

    Returns:
        JSON payload as a string.
    """
    text = raw.strip()
    match = _JSON_FENCE.search(text)
    if match:
        return match.group(1).strip()
    return text


def parse_typed_json[T: BaseModel](model: type[T], raw: str) -> T:
    """Parse JSON text into a validated Pydantic model.

    Args:
        model: Target model class.
        raw: Raw LLM completion text.

    Returns:
        Validated model instance.

    Raises:
        ValidationError: If JSON is invalid or does not match the schema.
    """
    blob = extract_json_text(raw)
    return model.model_validate_json(blob)


def parse_typed_json_lenient[T: BaseModel](
    model: type[T],
    raw: str,
) -> tuple[T | None, str | None]:
    """Parse JSON like :func:`parse_typed_json` but return errors instead of raising.

    Args:
        model: Target model class.
        raw: Raw LLM completion text.

    Returns:
        A tuple ``(instance, error)`` where ``error`` is set on failure.
    """
    try:
        return parse_typed_json(model, raw), None
    except (ValidationError, json.JSONDecodeError, ValueError) as exc:
        return None, str(exc)


class SpecialistExtractedFactModel(TaraModel):
    """One extracted fact from specialist LLM output."""

    claim: str = Field(min_length=1, max_length=2000)
    type: str = Field(default="chronology", max_length=64)
    confidence: str = Field(default="medium", max_length=16)
    supporting_chunk_ids: list[str] = Field(default_factory=list)
    uncertainty: str | None = Field(default=None, max_length=500)


class SpecialistExtractionPayload(TaraModel):
    """Structured specialist extraction response."""

    facts: list[SpecialistExtractedFactModel] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    rejected_noise: list[str] = Field(default_factory=list)


class ArbitrationLLMVerdict(TaraModel):
    """LLM arbitration outcome for a candidate conflict group."""

    is_contradiction: bool = Field(
        description="True only if claims are mutually exclusive.",
    )
    outcome: str = Field(
        description="One of: accepted, merged, uncertain, do_not_claim.",
        max_length=32,
    )
    accepted_answer_ids: list[str] = Field(default_factory=list)
    rejected_answer_ids: list[str] = Field(default_factory=list)
    merged_claim: str | None = Field(default=None, max_length=2000)
    basis: str = Field(default="", max_length=2000)


class ComposerSectionPayload(TaraModel):
    """One composed summary section."""

    section_id: str = Field(max_length=128)
    title: str = Field(max_length=256)
    content: str = Field(max_length=32_000)
    supporting_answer_ids: list[str] = Field(default_factory=list)


class ComposerLLMPayload(TaraModel):
    """Composer LLM structured response."""

    markdown: str = Field(max_length=64_000)
    sections: list[ComposerSectionPayload] = Field(default_factory=list)


class AuditLLMPayload(TaraModel):
    """Short adversarial audit verdict."""

    approved: bool
    issues: list[str] = Field(default_factory=list)


def claim_type_from_string(raw: str) -> ClaimType | None:
    """Map a loose type string to :class:`ClaimType` when possible.

    Args:
        raw: Type string from the model.

    Returns:
        Matching enum member, or ``None`` if unknown.
    """
    normalized = raw.strip().lower().replace("-", "_")
    aliases: dict[str, ClaimType] = {
        "chronology": ClaimType.CHRONOLOGY,
        "combat_outcome": ClaimType.COMBAT_OUTCOME,
        "character_state": ClaimType.CHARACTER_STATE,
        "quest_continuity": ClaimType.QUEST_CONTINUITY,
        "resource_state": ClaimType.RESOURCE_STATE,
        "final_state": ClaimType.FINAL_STATE,
    }
    return aliases.get(normalized)
