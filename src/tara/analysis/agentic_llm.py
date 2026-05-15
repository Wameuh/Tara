"""LLM-backed steps for the Tara blackboard analysis pipeline."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

from tara.analysis.llm_runner import LLMRequest, LLMResponse, LLMRunner
from tara.analysis.models import (
    AnalysisQuestion,
    ClaimType,
    Confidence,
    Conflict,
    EvidenceAnswer,
    EvidenceChunk,
    EvidenceSupport,
    FactStatus,
    SummaryDraft,
    SummarySection,
)
from tara.analysis.structured_output import (
    ArbitrationLLMVerdict,
    AuditLLMPayload,
    ComposerLLMPayload,
    SpecialistExtractionPayload,
    claim_type_from_string,
    extract_json_text,
    parse_typed_json_lenient,
)

LOGGER = logging.getLogger(__name__)

_CONFIDENCE_MAP: dict[str, Confidence] = {
    "high": Confidence.HIGH,
    "medium": Confidence.MEDIUM,
    "low": Confidence.LOW,
}


@dataclass(slots=True)
class LLMUsageDelta:
    """Incremental LLM usage counters for one backend call."""

    calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0


def _response_usage(response: LLMResponse) -> LLMUsageDelta:
    """Normalize usage fields from one LLM response."""
    tokens = response.total_tokens
    if tokens <= 0:
        tokens = max(0, response.input_tokens) + max(0, response.output_tokens)
    cost = float(response.estimated_cost_usd or 0.0)
    return LLMUsageDelta(calls=1, tokens=max(tokens, 0), cost_usd=cost)


def _merge_usage(a: LLMUsageDelta, b: LLMUsageDelta) -> LLMUsageDelta:
    """Sum incremental usage from two backend calls."""
    return LLMUsageDelta(
        calls=a.calls + b.calls,
        tokens=a.tokens + b.tokens,
        cost_usd=a.cost_usd + b.cost_usd,
    )


def parse_with_single_json_repair[T: BaseModel](
    model: type[T],
    raw: str,
    llm_runner: LLMRunner,
    *,
    repair_purpose_prefix: str,
    schema_description: str,
    initial_usage: LLMUsageDelta,
) -> tuple[T | None, LLMUsageDelta]:
    """Parse JSON; on failure run exactly one repair completion and re-parse.

    Args:
        model: Pydantic model for the expected payload.
        raw: Raw completion text from the primary LLM call.
        llm_runner: Runner used for the optional repair pass.
        repair_purpose_prefix: Base ``purpose`` for telemetry; ``.json_repair``
            is appended for the repair request.
        schema_description: Short human-readable schema hint for the repair
            prompt.
        initial_usage: Usage already accrued from the primary call.

    Returns:
        Parsed instance (or ``None``) and merged usage including any repair
        call.
    """
    parsed, err = parse_typed_json_lenient(model, raw)
    if parsed is not None:
        return parsed, initial_usage

    snippet = extract_json_text(raw)
    max_chars = 12_000
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "\n... (truncated)"

    repair_request = LLMRequest(
        purpose=f"{repair_purpose_prefix}.json_repair",
        system_prompt=(
            "You output a single valid JSON object only. "
            "No markdown code fences, no commentary before or after."
        ),
        user_prompt=(
            "The following text was meant to be JSON for this target schema:\n"
            f"{schema_description}\n\n"
            f"Parse/validation error:\n{err}\n\n"
            "Rewrite it into one valid JSON object that satisfies the schema.\n"
            "Invalid or partial output:\n"
            f"{snippet}"
        ),
        temperature=0.0,
    )
    try:
        repair_response = llm_runner.run(repair_request)
    except Exception as exc:
        LOGGER.warning(
            "JSON repair LLM call failed for %s: %s",
            repair_purpose_prefix,
            exc,
        )
        return None, initial_usage

    merged = _merge_usage(initial_usage, _response_usage(repair_response))
    parsed2, err2 = parse_typed_json_lenient(model, repair_response.content)
    if parsed2 is None:
        LOGGER.warning(
            "JSON repair did not yield a valid parse for %s: %s",
            repair_purpose_prefix,
            err2,
        )
    return parsed2, merged


def run_specialist_extraction(
    llm_runner: LLMRunner,
    *,
    question: AnalysisQuestion,
    chunks: list[EvidenceChunk],
    default_claim_type: ClaimType,
    is_critical_default: bool,
) -> tuple[list[EvidenceAnswer], LLMUsageDelta]:
    """Call the LLM once to extract structured facts from evidence chunks.

    Args:
        llm_runner: Configured runner.
        question: Planned analysis question.
        chunks: Evidence chunks shown to the model.
        default_claim_type: Fallback claim type from the specialist role.
        is_critical_default: Whether facts inherit criticality from the question.

    Returns:
        Parsed evidence answers and usage delta.
    """
    if not chunks:
        return [], LLMUsageDelta()

    chunk_payload = [
        {
            "chunk_id": chunk.chunk_id,
            "start": chunk.start,
            "end": chunk.end,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    q_meta = json.dumps(question.model_dump(mode="json"), ensure_ascii=True)
    chunks_json = json.dumps(chunk_payload, ensure_ascii=True, indent=2)
    user_prompt = (
        "You extract tabletop RPG session facts from evidence chunks only.\n"
        "Return strict JSON matching this schema:\n"
        '{"facts":[{"claim":"string","type":"chronology|combat_outcome|character_state|'
        'quest_continuity|resource_state|final_state","confidence":"high|medium|low",'
        '"supporting_chunk_ids":["chunk_id",...],"uncertainty":null|string}],'
        '"open_questions":[],"rejected_noise":[]}\n'
        "Rules:\n"
        "- Every factual claim must cite at least one supporting_chunk_id "
        "from the input.\n"
        "- Do not prefix claims with agent names.\n"
        "- Do not copy long raw transcript quotes as claims; "
        "synthesize short factual claims.\n"
        "- If evidence is weak, return fewer facts or mark uncertainty.\n\n"
        f"Question metadata: {q_meta}\n\n"
        f"Evidence chunks JSON:\n{chunks_json}"
    )
    request = LLMRequest(
        purpose="analysis.specialist_extraction",
        system_prompt=(
            "You are a structured information extraction assistant. "
            "Reply with JSON only, no markdown fences."
        ),
        user_prompt=user_prompt,
        temperature=0.0,
        metadata={"question_id": question.question_id},
    )
    response = llm_runner.run(request)
    usage = _response_usage(response)
    parsed, usage = parse_with_single_json_repair(
        SpecialistExtractionPayload,
        response.content,
        llm_runner,
        repair_purpose_prefix="analysis.specialist_extraction",
        schema_description=(
            '{"facts":[{"claim","type","confidence","supporting_chunk_ids",'
            '"uncertainty"}...],"open_questions":[],"rejected_noise":[]}'
        ),
        initial_usage=usage,
    )
    if parsed is None:
        LOGGER.warning(
            "Specialist JSON parse failed for %s after repair",
            question.question_id,
        )
        return [], usage

    allowed_ids = {chunk.chunk_id for chunk in chunks}
    answers: list[EvidenceAnswer] = []
    for index, row in enumerate(parsed.facts):
        if any(cid not in allowed_ids for cid in row.supporting_chunk_ids):
            LOGGER.warning(
                "Specialist fact dropped due to unknown chunk ids: %s",
                row.supporting_chunk_ids,
            )
            continue
        if _looks_like_agent_prefixed_claim(row.claim):
            continue
        ct = claim_type_from_string(row.type) or default_claim_type
        conf = _CONFIDENCE_MAP.get(row.confidence.strip().lower(), Confidence.MEDIUM)
        support = _support_from_chunk_ids(chunks, row.supporting_chunk_ids)
        if not support:
            continue
        answers.append(
            EvidenceAnswer(
                answer_id=f"{question.question_id}_llm_{index:02d}",
                question_id=question.question_id,
                claim=row.claim.strip(),
                status=FactStatus.SUPPORTED,
                importance=4 if is_critical_default else 3,
                support=support,
                confidence=conf,
                claim_type=ct,
                is_critical=is_critical_default,
                metadata={"source": "llm_extraction"},
            ),
        )
    if not answers:
        answers.append(
            EvidenceAnswer(
                answer_id=f"{question.question_id}_uncertain",
                question_id=question.question_id,
                claim=f"No validated LLM facts for {question.question_id}.",
                status=FactStatus.UNCERTAIN,
                importance=question.priority,
                confidence=Confidence.LOW,
                claim_type=default_claim_type,
                notes="LLM extraction returned no chunk-backed facts.",
            ),
        )
    return answers, usage


def run_composer_llm(
    llm_runner: LLMRunner,
    *,
    facts_payload: list[dict[str, Any]],
    do_not_claim: list[str],
) -> tuple[SummaryDraft, LLMUsageDelta]:
    """Compose the session summary markdown from accepted facts via LLM.

    Args:
        llm_runner: Configured runner.
        facts_payload: JSON-serializable accepted fact rows.
        do_not_claim: Forbidden formulations.

    Returns:
        A validated draft and usage delta.
    """
    forbidden_json = json.dumps(do_not_claim, ensure_ascii=True)
    facts_json = json.dumps(facts_payload, ensure_ascii=True, indent=2)
    user_prompt = (
        "Compose a French campaign session summary from supported facts only.\n"
        "The markdown MUST include these headings exactly (including accents):\n"
        "# Résumé de session\n"
        "## Résumé express\n"
        "## Impacts pour la suite\n"
        "## État final et ressources\n"
        "Each section must use supporting_answer_ids from the input facts only.\n"
        "Return strict JSON with keys markdown (full document) and sections "
        "(list of {section_id,title,content,supporting_answer_ids}).\n"
        "Do not include internal agent labels such as 'ChronologyAgent:'.\n\n"
        f"Forbidden claims (do not restate): {forbidden_json}\n\n"
        f"Facts JSON:\n{facts_json}"
    )
    request = LLMRequest(
        purpose="analysis.summary_composer",
        system_prompt=(
            "You are a careful editor for tabletop RPG session notes. "
            "Reply with JSON only, no markdown fences."
        ),
        user_prompt=user_prompt,
        temperature=0.2,
    )
    response = llm_runner.run(request)
    usage = _response_usage(response)
    parsed, usage = parse_with_single_json_repair(
        ComposerLLMPayload,
        response.content,
        llm_runner,
        repair_purpose_prefix="analysis.summary_composer",
        schema_description=(
            '{"markdown":"French session summary markdown",'
            '"sections":[{"section_id","title","content","supporting_answer_ids"}]}'
        ),
        initial_usage=usage,
    )
    if parsed is None:
        LOGGER.warning(
            "Composer LLM JSON invalid after repair, using fallback draft",
        )
        return _fallback_composer_draft(facts_payload, do_not_claim), usage
    sections = [
        SummarySection(
            section_id=s.section_id,
            title=s.title,
            content=s.content,
            supporting_answer_ids=list(s.supporting_answer_ids),
        )
        for s in parsed.sections
    ]
    draft = SummaryDraft(
        markdown=parsed.markdown.strip(),
        sections=sections,
        forbidden_claim_ids=list(do_not_claim),
        metadata={"source": "llm_composer"},
    )
    return draft, usage


def run_audit_llm(
    llm_runner: LLMRunner,
    *,
    draft_markdown: str,
    facts_payload: list[dict[str, Any]],
    do_not_claim: list[str],
) -> tuple[list[str], LLMUsageDelta]:
    """Run a short semantic audit pass.

    Args:
        llm_runner: Configured runner.
        draft_markdown: Final or draft markdown.
        facts_payload: Accepted facts for cross-check.
        do_not_claim: Forbidden claims list.

    Returns:
        A list of issue strings (empty if approved) and usage delta.
    """
    user_prompt = (
        "Audit this French session summary for tabletop RPG continuity.\n"
        "Reject if you see internal agent labels like 'SomethingAgent:'.\n"
        "Reject if required headings are missing: "
        "'# Résumé de session', '## Résumé express', "
        "'## Impacts pour la suite', '## État final et ressources'.\n"
        "Reject if bullets look like raw transcript dumps rather than synthesis.\n"
        'Return JSON {"approved":bool,"issues":["..."]}\n\n'
        f"Do-not-claim list: {json.dumps(do_not_claim, ensure_ascii=True)}\n\n"
        f"Facts JSON:\n{json.dumps(facts_payload, ensure_ascii=True)}\n\n"
        f"Summary markdown:\n{draft_markdown}"
    )
    request = LLMRequest(
        purpose="analysis.adversarial_audit",
        system_prompt="You are a strict QA reviewer. Reply with JSON only.",
        user_prompt=user_prompt,
        temperature=0.0,
    )
    response = llm_runner.run(request)
    usage = _response_usage(response)
    parsed, usage = parse_with_single_json_repair(
        AuditLLMPayload,
        response.content,
        llm_runner,
        repair_purpose_prefix="analysis.adversarial_audit",
        schema_description='{"approved":bool,"issues":["string",...]}',
        initial_usage=usage,
    )
    if parsed is None:
        return ["audit_json_error: parse failed after repair"], usage
    if parsed.approved:
        return [], usage
    return list(parsed.issues), usage


def run_arbitration_llm(
    llm_runner: LLMRunner,
    *,
    conflict: Conflict,
    fact_rows: list[dict[str, Any]],
) -> tuple[ArbitrationLLMVerdict | None, LLMUsageDelta]:
    """Ask the LLM whether competing claims are contradictory.

    Args:
        llm_runner: Configured runner.
        conflict: Detected conflict metadata.
        fact_rows: Serialized facts in the group.

    Returns:
        Parsed verdict or ``None`` on failure, plus usage delta.
    """
    user_prompt = (
        "Determine whether these supported claims are complementary details, "
        "duplicates, or genuine contradictions for a tabletop RPG session.\n"
        "Return JSON with keys: is_contradiction (bool), outcome "
        "(accepted|merged|uncertain|do_not_claim), accepted_answer_ids, "
        "rejected_answer_ids, merged_claim (optional string), basis (string).\n\n"
        f"Conflict id: {conflict.conflict_id}\n"
        f"Facts JSON:\n{json.dumps(fact_rows, ensure_ascii=True, indent=2)}"
    )
    request = LLMRequest(
        purpose="analysis.arbitration",
        system_prompt="You arbitrate factual consistency. Reply with JSON only.",
        user_prompt=user_prompt,
        temperature=0.0,
    )
    response = llm_runner.run(request)
    usage = _response_usage(response)
    parsed, usage = parse_with_single_json_repair(
        ArbitrationLLMVerdict,
        response.content,
        llm_runner,
        repair_purpose_prefix="analysis.arbitration",
        schema_description=(
            '{"is_contradiction":bool,"outcome":"accepted|merged|uncertain|'
            'do_not_claim","accepted_answer_ids":[],"rejected_answer_ids":[],'
            '"merged_claim":null|string,"basis":string}'
        ),
        initial_usage=usage,
    )
    if parsed is None:
        return None, usage
    return parsed, usage


def _looks_like_agent_prefixed_claim(claim: str) -> bool:
    """Return True when a claim looks like an internal agent debug prefix."""
    stripped = claim.strip()
    if ":" not in stripped:
        return False
    head = stripped.split(":", 1)[0].strip()
    return head.endswith("Agent")


def _support_from_chunk_ids(
    chunks: list[EvidenceChunk],
    chunk_ids: list[str],
) -> list[EvidenceSupport]:
    """Build evidence supports for the referenced chunk ids."""
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    support: list[EvidenceSupport] = []
    for cid in chunk_ids:
        chunk = by_id.get(cid)
        if chunk is None:
            continue
        support.append(
            EvidenceSupport(
                chunk_id=chunk.chunk_id,
                start=chunk.start,
                end=chunk.end,
                segment_ids=list(chunk.segment_ids),
            ),
        )
    return support


def _fallback_composer_draft(
    facts_payload: list[dict[str, Any]],
    do_not_claim: list[str],
) -> SummaryDraft:
    """Build a minimal ARCHITECTURE-shaped draft when LLM JSON is unusable."""
    ids = [str(row.get("answer_id")) for row in facts_payload if row.get("answer_id")]
    if not ids:
        ids = ["composer_fallback_placeholder"]
    bullets = "\n".join(
        f"- {row.get('claim', '').strip()}"
        for row in facts_payload[:12]
        if row.get("claim")
    )
    markdown = (
        "# Résumé de session\n\n"
        "## Résumé express\n"
        f"{bullets or '- (aucun fait synthétisé)'}\n\n"
        "## Impacts pour la suite\n"
        f"{bullets or '- (à compléter)'}\n\n"
        "## État final et ressources\n"
        f"{bullets or '- (à compléter)'}\n"
    )
    section = SummarySection(
        section_id="resume_express",
        title="Résumé express",
        content=bullets or "-",
        supporting_answer_ids=ids,
    )
    impacts = SummarySection(
        section_id="impacts",
        title="Impacts pour la suite",
        content=bullets or "-",
        supporting_answer_ids=ids,
    )
    final_state = SummarySection(
        section_id="final_state",
        title="État final et ressources",
        content=bullets or "-",
        supporting_answer_ids=ids,
    )
    return SummaryDraft(
        markdown=markdown,
        sections=[section, impacts, final_state],
        forbidden_claim_ids=list(do_not_claim),
        metadata={"source": "composer_fallback", "composer_json_error": True},
    )


def blackboard_facts_to_payload(blackboard: object) -> list[dict[str, Any]]:
    """Serialize supported blackboard facts for LLM prompts."""
    from tara.analysis.agents import BlackboardState

    board = cast(BlackboardState, blackboard)
    rows: list[dict[str, Any]] = []
    for fact in board.facts:
        if fact.status != FactStatus.SUPPORTED or fact.do_not_claim:
            continue
        if not fact.answer_id:
            continue
        rows.append(
            {
                "answer_id": fact.answer_id,
                "claim": fact.claim,
                "claim_type": fact.claim_type.value if fact.claim_type else None,
                "importance": fact.importance,
                "is_critical": fact.is_critical,
                "confidence": fact.confidence.value,
                "chunk_ids": [s.chunk_id for s in fact.support],
            },
        )
    return rows
