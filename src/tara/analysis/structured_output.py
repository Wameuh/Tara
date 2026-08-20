"""Strict YAML parsing for agentic LLM responses."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, ValidationError
from ruamel.yaml.error import YAMLError

from tara.analysis.models import ClaimType, TaraModel
from tara.yaml_utils import parse_yaml_with_repair, repair_yaml_blob

_YAML_FENCE = re.compile(r"```(?:yaml|yml|json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def extract_yaml_text(raw: str) -> str:
    """Strip markdown fences and return YAML text for parsing.

    Args:
        raw: Raw LLM completion text.

    Returns:
        YAML payload as a string.
    """
    text = raw.strip()
    match = _YAML_FENCE.search(text)
    if match:
        return match.group(1).strip()
    return text


def parse_typed_yaml[T: BaseModel](model: type[T], raw: str) -> T:
    """Parse YAML text into a validated Pydantic model.

    Args:
        model: Target model class.
        raw: Raw LLM completion text.

    Returns:
        Validated model instance.

    Raises:
        ValidationError: If YAML is invalid or does not match the schema.
        YAMLError: If YAML parsing fails.
    """
    blob = extract_yaml_text(raw)
    data = parse_yaml_with_repair(blob)
    return model.model_validate(data)


def parse_typed_yaml_lenient[T: BaseModel](
    model: type[T],
    raw: str,
) -> tuple[T | None, str | None]:
    """Parse YAML like :func:`parse_typed_yaml` but return errors instead of raising.

    Args:
        model: Target model class.
        raw: Raw LLM completion text.

    Returns:
        A tuple ``(instance, error)`` where ``error`` is set on failure.
    """
    try:
        return parse_typed_yaml(model, raw), None
    except (ValidationError, YAMLError, ValueError, TypeError) as exc:
        return None, str(exc)


def parse_typed_json[T: BaseModel](model: type[T], raw: str) -> T:
    """Backward-compatible alias for :func:`parse_typed_yaml`."""
    return parse_typed_yaml(model, raw)


def parse_typed_json_lenient[T: BaseModel](
    model: type[T],
    raw: str,
) -> tuple[T | None, str | None]:
    """Backward-compatible alias for :func:`parse_typed_yaml_lenient`."""
    return parse_typed_yaml_lenient(model, raw)


def extract_json_text(raw: str) -> str:
    """Backward-compatible alias that returns repaired YAML text."""
    return repair_yaml_blob(extract_yaml_text(raw))


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
        # Common scene-description aliases from LLM outputs.
        "location": ClaimType.CHRONOLOGY,
        "terrain": ClaimType.CHRONOLOGY,
        "action": ClaimType.CHRONOLOGY,
        "entity": ClaimType.CHARACTER_STATE,
        "state_change": ClaimType.CHARACTER_STATE,
        "state": ClaimType.CHARACTER_STATE,
        "combat": ClaimType.COMBAT_OUTCOME,
        "outcome": ClaimType.COMBAT_OUTCOME,
        "tactics": ClaimType.COMBAT_OUTCOME,
        "mechanics": ClaimType.RESOURCE_STATE,
        "resource": ClaimType.RESOURCE_STATE,
        "quest": ClaimType.QUEST_CONTINUITY,
    }
    return aliases.get(normalized)
