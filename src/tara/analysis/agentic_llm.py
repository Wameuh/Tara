"""LLM-backed steps for the Tara blackboard analysis pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
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
    extract_yaml_text,
    parse_typed_yaml_lenient,
)
from tara.yaml_utils import to_yaml

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


def parse_with_single_yaml_repair[T: BaseModel](
    model: type[T],
    raw: str,
    llm_runner: LLMRunner,
    *,
    repair_purpose_prefix: str,
    schema_description: str,
    initial_usage: LLMUsageDelta,
) -> tuple[T | None, LLMUsageDelta]:
    """Parse YAML; on failure run exactly one repair completion and re-parse.

    Args:
        model: Pydantic model for the expected payload.
        raw: Raw completion text from the primary LLM call.
        llm_runner: Runner used for the optional repair pass.
        repair_purpose_prefix: Base ``purpose`` for telemetry; ``.yaml_repair``
            is appended for the repair request.
        schema_description: Short human-readable schema hint for the repair
            prompt.
        initial_usage: Usage already accrued from the primary call.

    Returns:
        Parsed instance (or ``None``) and merged usage including any repair
        call.
    """
    parsed, err = parse_typed_yaml_lenient(model, raw)
    if parsed is not None:
        return parsed, initial_usage

    snippet = extract_yaml_text(raw)
    max_chars = 12_000
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "\n... (truncated)"

    repair_request = LLMRequest(
        purpose=f"{repair_purpose_prefix}.yaml_repair",
        system_prompt=(
            "You output a single valid YAML mapping only. "
            "No markdown code fences, no commentary before or after."
        ),
        user_prompt=(
            "The following text was meant to be YAML for this target schema:\n"
            f"{schema_description}\n\n"
            f"Parse/validation error:\n{err}\n\n"
            "Rewrite it into one valid YAML mapping that satisfies the schema.\n"
            "Invalid or partial output:\n"
            f"{snippet}"
        ),
        temperature=0.0,
    )
    try:
        repair_response = llm_runner.run(repair_request)
    except Exception as exc:
        LOGGER.warning(
            "YAML repair LLM call failed for %s: %s",
            repair_purpose_prefix,
            exc,
        )
        return None, initial_usage

    merged = _merge_usage(initial_usage, _response_usage(repair_response))
    parsed2, err2 = parse_typed_yaml_lenient(model, repair_response.content)
    if parsed2 is None:
        LOGGER.warning(
            "YAML repair did not yield a valid parse for %s: %s",
            repair_purpose_prefix,
            err2,
        )
    return parsed2, merged


parse_with_single_json_repair = parse_with_single_yaml_repair


def run_specialist_extraction(
    llm_runner: LLMRunner,
    *,
    question: AnalysisQuestion,
    chunks: list[EvidenceChunk],
    default_claim_type: ClaimType,
    is_critical_default: bool,
    context_text: str | None = None,
    transcription_path: Path | None = None,
    cursor_cli_specialist_tool: bool = False,
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
    q_meta = to_yaml(question.model_dump(mode="json"))
    chunks_yaml = to_yaml(chunk_payload)
    user_prompt = (
        "You extract tabletop RPG session facts from evidence chunks only.\n"
        "Return strict YAML matching this schema:\n"
        "facts:\n"
        "  - claim: string\n"
        "    type: chronology|combat_outcome|character_state|quest_continuity|"
        "resource_state|final_state\n"
        "    confidence: high|medium|low\n"
        "    supporting_chunk_ids:\n"
        "      - chunk_id\n"
        "    uncertainty: null or string\n"
        "open_questions: []\n"
        "rejected_noise: []\n"
        "Rules:\n"
        "- Every factual claim must cite at least one supporting_chunk_id "
        "from the input.\n"
        "- Do not prefix claims with agent names.\n"
        "- Do not copy long raw transcript quotes as claims; "
        "synthesize short factual claims.\n"
        "- If evidence is weak, return fewer facts or mark uncertainty.\n\n"
        f"{_specialist_tool_block(
            chunks,
            transcription_path,
            cursor_cli_specialist_tool,
        )}"
        f"Question metadata:\n{q_meta}\n\n"
        f"Evidence chunks YAML:\n{chunks_yaml}"
    )
    request = LLMRequest(
        purpose="analysis.specialist_extraction",
        stage="specialist",
        system_prompt=build_specialist_system_prompt(context_text),
        user_prompt=user_prompt,
        temperature=0.0,
        metadata={"question_id": question.question_id},
    )
    response = llm_runner.run(request)
    usage = _response_usage(response)
    parsed, usage = parse_with_single_yaml_repair(
        SpecialistExtractionPayload,
        response.content,
        llm_runner,
        repair_purpose_prefix="analysis.specialist_extraction",
        schema_description=(
            "facts:\n"
            "  - claim, type, confidence, supporting_chunk_ids, uncertainty\n"
            "open_questions: []\n"
            "rejected_noise: []"
        ),
        initial_usage=usage,
    )
    if parsed is None:
        LOGGER.warning(
            "Specialist YAML parse failed for %s after repair",
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
    scene_timeline: object | None = None,
    context_text: str | None = None,
    prior_context_text: str | None = None,
) -> tuple[SummaryDraft, LLMUsageDelta]:
    """Compose the session summary markdown from accepted facts via LLM.

    Args:
        llm_runner: Configured runner.
        facts_payload: JSON-serializable accepted fact rows.
        do_not_claim: Forbidden formulations.

    Returns:
        A validated draft and usage delta.
    """
    forbidden_yaml = to_yaml(do_not_claim)
    facts_yaml = to_yaml(facts_payload)
    user_prompt = (
        "Compose a French campaign session summary from supported facts only.\n"
        "The goal is to remind players what happened before the next session: "
        "give them a clear overview of the story, stakes, scene state, and "
        "important consequences.\n"
        "Use the scene timeline as the narrative backbone when it is provided. "
        "Do not flatten the session into disconnected facts; preserve the "
        "progression of major scenes and the key actions that shaped them.\n"
        "The 'Résumé express' section must stay concise but not skeletal: aim "
        "for 5 to 7 broad phase groups, usually two compact sentences per "
        "group. One sentence is fine for a simple transition; avoid more than "
        "three short sentences in a group. A good two-sentence group is: "
        "sentence one names the major event/outcome, sentence two names the "
        "lasting consequence or transition. Do not produce a blow-by-blow "
        "complete recap in that section.\n"
        "For combat phases, state who was fought and the outcome only. Do not "
        "narrate individual attacks, spells, kills, or positioning unless they "
        "create a lasting consequence players must remember (especially a player "
        "character KO or unconsciousness).\n"
        "Do not include dice rolls, attack totals, save DCs, initiative order, "
        "or opportunity attacks unless that mechanical detail directly changes "
        "the story state, a character's final condition, or a resource players "
        "must remember.\n"
        "Keep low-level mechanics out of 'Résumé express': do not mention Ki, "
        "action economy, exact movement limits, exact positioning, temporary "
        "combat modifiers, or per-attack details there unless they are the "
        "main story consequence. Put essential remaining resources in "
        "'État final et ressources' instead.\n"
        "In 'Résumé express', avoid bookkeeping examples such as temporary hit "
        "point amounts, exact healing numbers, spell-slot accounting, named "
        "concentration bookkeeping, specific turret/ballista mechanics, weapon "
        "string failures, or special movement rules. Summarize their narrative "
        "effect instead, such as 'the group falls back', 'support magic is "
        "spent', or 'the party is badly wounded'.\n"
        "Avoid low-value details such as isolated missed attacks, exact rolls "
        "to hit, or transient positioning when they do not affect the next "
        "session.\n"
        "The markdown MUST include these headings exactly (including accents):\n"
        "# Résumé de session\n"
        "## Résumé express\n"
        "## Impacts pour la suite\n"
        "## État final et ressources\n"
        "Do not add any other markdown heading, including 'Non-confirmed' or "
        "debug/audit sections.\n"
        "After '# Résumé de session', include at most one short orientation "
        "sentence before '## Résumé express'. Do not put a complete recap "
        "before the first section; 'Résumé express' must be the first "
        "substantive player-facing content.\n"
        "Each section must use supporting_answer_ids from the input facts only.\n"
        "Do not print answer IDs, chunk IDs, scene IDs, or evidence citations in "
        "the markdown text. Those references belong only in the JSON "
        "supporting_answer_ids fields.\n"
        "Return strict YAML with keys markdown (full document) and sections "
        "(list of section_id, title, content, supporting_answer_ids).\n"
        "Do not include internal agent labels such as 'ChronologyAgent:'.\n\n"
        "Use character names and MJ in the final summary when the general "
        "context makes those names clear. Avoid player pseudonyms in the final "
        "summary. If a character attribution is uncertain, stay neutral rather "
        "than guessing. If Facts JSON contains neutral wording for a hit, fall, "
        "condition, spell, or other action, do not replace it with a named "
        "character from the scene timeline.\n\n"
        f"{_scene_timeline_block(scene_timeline)}"
        f"Forbidden claims (do not restate):\n{forbidden_yaml}\n\n"
        f"Facts YAML:\n{facts_yaml}"
    )
    request = LLMRequest(
        purpose="analysis.summary_composer",
        stage="composer",
        system_prompt=build_composer_system_prompt(
            context_text=context_text,
            prior_context_text=prior_context_text,
        ),
        user_prompt=user_prompt,
        temperature=0.2,
    )
    response = llm_runner.run(request)
    usage = _response_usage(response)
    parsed, usage = parse_with_single_yaml_repair(
        ComposerLLMPayload,
        response.content,
        llm_runner,
        repair_purpose_prefix="analysis.summary_composer",
        schema_description=(
            "markdown: French session summary markdown\n"
            "sections:\n"
            "  - section_id, title, content, supporting_answer_ids"
        ),
        initial_usage=usage,
    )
    if parsed is None:
        LOGGER.warning(
            "Composer LLM YAML invalid after repair, using fallback draft",
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
    scene_timeline: object | None = None,
    context_text: str | None = None,
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
        "Reject if there is more than one short orientation sentence between "
        "'# Résumé de session' and '## Résumé express'; the first substantive "
        "content must be the 'Résumé express' section.\n"
        "Reject if markdown text exposes answer IDs, chunk IDs, scene IDs, or "
        "evidence citations; those are internal traceability only.\n"
        "Reject if bullets look like raw transcript dumps rather than synthesis.\n"
        "Reject if the summary over-focuses on low-impact mechanics such as "
        "dice rolls, attack totals, isolated opportunity attacks, or initiative "
        "order instead of player-facing story overview and consequences.\n"
        "Reject if 'Résumé express' reads like a complete recap: groups should "
        "usually be two compact sentences, not blow-by-blow paragraphs, and "
        "should not include low-level mechanics such as Ki spending, action "
        "economy, exact movement limits, exact positioning, transient combat "
        "modifiers, or per-attack details unless they are the main consequence.\n"
        "Reject if a combat phase narrates individual attacks, spells, per-enemy "
        "kills, positioning, or round-by-round exchanges instead of stating the "
        "opponent and outcome. Mention combat details only for lasting "
        "consequences such as a player character KO or unconsciousness.\n"
        "Reject if 'Résumé express' includes bookkeeping details such as "
        "temporary hit point amounts, exact healing numbers, spell-slot "
        "accounting, named concentration bookkeeping, turret/ballista mechanics, "
        "weapon string failures, or special movement rules when a narrative "
        "effect would be enough.\n"
        "Reject if the evidence indicates multiple distinct scenes but the "
        "summary collapses them into one event.\n"
        "Reject if supported aftermath or end-of-session consequences are omitted "
        "from the narrative arc.\n"
        "When a scene timeline is provided, lightly check for major omitted "
        "scene consequences, but do not require every scene to be named.\n"
        "Check that clear player pseudonyms from evidence are normalized to "
        "character names or MJ when the general context provides that mapping.\n"
        "Reject if a player character is credited with an action that is only "
        "weakly inferred from an audio speaker label, an out-of-character joke, "
        "or MJ narration. Audio speaker labels are evidence provenance, not "
        "proof of who acted in the story.\n"
        'Return YAML with approved: bool and issues: ["..."]\n\n'
        f"{_scene_timeline_block(scene_timeline)}"
        f"Do-not-claim list:\n{to_yaml(do_not_claim)}\n\n"
        f"Facts YAML:\n{to_yaml(facts_payload)}\n\n"
        f"Summary markdown:\n{draft_markdown}"
    )
    request = LLMRequest(
        purpose="analysis.adversarial_audit",
        stage="audit",
        system_prompt=build_audit_system_prompt(context_text),
        user_prompt=user_prompt,
        temperature=0.0,
    )
    response = llm_runner.run(request)
    usage = _response_usage(response)
    parsed, usage = parse_with_single_yaml_repair(
        AuditLLMPayload,
        response.content,
        llm_runner,
        repair_purpose_prefix="analysis.adversarial_audit",
        schema_description="approved: bool\nissues:\n  - string",
        initial_usage=usage,
    )
    if parsed is None:
        return ["audit_yaml_error: parse failed after repair"], usage
    if parsed.approved:
        return [], usage
    return list(parsed.issues), usage


def run_arbitration_llm(
    llm_runner: LLMRunner,
    *,
    conflict: Conflict,
    fact_rows: list[dict[str, Any]],
    context_text: str | None = None,
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
        "Return YAML with keys: is_contradiction (bool), outcome "
        "(accepted|merged|uncertain|do_not_claim), accepted_answer_ids, "
        "rejected_answer_ids, merged_claim (optional string), basis (string).\n\n"
        "Actor conflict rule: if claims agree on the event but disagree about "
        "which character acted, was hit, fell, cast a spell, or suffered a "
        "condition, do not choose a named actor from noisy ASR/proper-name "
        "snippets. Prefer an already-supported neutral claim that says "
        "'a character', 'a party member', or 'the group'; reject the "
        "actor-specific variants. If no neutral claim exists, outcome should "
        "be uncertain or merged with a neutral merged_claim.\n\n"
        f"Conflict id: {conflict.conflict_id}\n"
        f"Facts YAML:\n{to_yaml(fact_rows)}"
    )
    request = LLMRequest(
        purpose="analysis.arbitration",
        stage="arbitration",
        system_prompt=build_arbitration_system_prompt(context_text),
        user_prompt=user_prompt,
        temperature=0.0,
    )
    response = llm_runner.run(request)
    usage = _response_usage(response)
    parsed, usage = parse_with_single_yaml_repair(
        ArbitrationLLMVerdict,
        response.content,
        llm_runner,
        repair_purpose_prefix="analysis.arbitration",
        schema_description=(
            "is_contradiction: bool\n"
            "outcome: accepted|merged|uncertain|do_not_claim\n"
            "accepted_answer_ids: []\n"
            "rejected_answer_ids: []\n"
            "merged_claim: null or string\n"
            "basis: string"
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


def build_specialist_system_prompt(context_text: str | None) -> str:
    """Build the stable specialist system prompt for cacheable LLM calls."""
    return (
        "You are a structured information extraction assistant. "
        "Reply with YAML only, no markdown fences.\n\n"
        f"{_general_context_block(context_text)}"
        f"{_evidence_strictness_policy_block()}"
        f"{_speaker_attribution_policy_block()}"
    )


def build_composer_system_prompt(
    *,
    context_text: str | None,
    prior_context_text: str | None,
) -> str:
    """Build the stable composer system prompt for cacheable LLM calls."""
    return (
        "You are a careful editor for tabletop RPG session notes. "
        "Reply with YAML only, no markdown fences.\n\n"
        f"{_prior_context_block(prior_context_text)}"
        f"{_general_context_block(context_text)}"
        f"{_evidence_strictness_policy_block()}"
        f"{_speaker_attribution_policy_block()}"
        f"{_combat_summarization_policy_block()}"
    )


def build_audit_system_prompt(context_text: str | None) -> str:
    """Build the stable audit system prompt for cacheable LLM calls."""
    return (
        "You are a strict QA reviewer. Reply with YAML only.\n\n"
        f"{_general_context_block(context_text)}"
        f"{_evidence_strictness_policy_block()}"
        f"{_speaker_attribution_policy_block()}"
        f"{_combat_summarization_policy_block()}"
    )


def build_arbitration_system_prompt(context_text: str | None) -> str:
    """Build the stable arbitration system prompt for cacheable LLM calls."""
    return (
        "You arbitrate factual consistency. Reply with YAML only.\n\n"
        f"{_general_context_block(context_text)}"
        f"{_evidence_strictness_policy_block()}"
        f"{_speaker_attribution_policy_block()}"
    )


def _specialist_tool_block(
    chunks: list[EvidenceChunk],
    transcription_path: Path | None,
    enabled: bool,
) -> str:
    """Return optional on-demand excerpt tool instructions for specialists."""
    if not enabled or transcription_path is None:
        return ""
    script_path = (
        Path(__file__).resolve().parents[3]
        / ".cursor"
        / "skills"
        / "tara-evidence"
        / "scripts"
        / "get_excerpt.py"
    )
    if not script_path.is_file():
        return ""
    ranges = sorted({(chunk.start, chunk.end) for chunk in chunks})
    range_lines = "\n".join(
        f"- {start:.1f}s to {end:.1f}s" for start, end in ranges
    )
    return (
        "Optional transcript excerpt tool:\n"
        "If the evidence chunks are insufficient, you may run this command to "
        "fetch additional transcript rows by timestamp:\n"
        f'python "{script_path}" '
        f'--transcription "{transcription_path.resolve()}" '
        "--start <seconds> --end <seconds> [--pad 15]\n"
        "Candidate timestamp ranges from the seed chunks:\n"
        f"{range_lines}\n"
        "The seed evidence chunks below remain authoritative; use the tool only "
        "to add missing context.\n\n"
    )


def _general_context_block(context_text: str | None) -> str:
    """Return the reusable prompt block for general campaign context."""
    if not context_text or not context_text.strip():
        return ""
    return (
        "General campaign context, provided by the user as reference only. "
        "Use it to normalize character names, aliases, players, and MJ. "
        "The transcript evidence remains authoritative for session events; "
        "do not invent events from this context.\n"
        "--- general context ---\n"
        f"{context_text.strip()}\n"
        "--- end general context ---\n\n"
    )


def _speaker_attribution_policy_block() -> str:
    """Return attribution rules shared by LLM-backed analysis steps."""
    return (
        "Speaker attribution policy:\n"
        "- Audio speaker labels identify the recording track owner, not "
        "necessarily the in-story actor.\n"
        "- The MJ/DM speaker may narrate any NPC, adjudicate any player "
        "action, repeat a player's declaration, or joke out of character; do "
        "not turn MJ first-person phrasing into an MJ character action.\n"
        "- A player speaker may talk about another character, quote someone, "
        "ask rules questions, or joke out of character. Treat the "
        "speaker-to-character mapping as a weak clue only.\n"
        "- ASR may mangle French fantasy names and second-person narration; "
        "phonetic fragments or near-name variants are weak evidence by "
        "themselves.\n"
        "- Attribute an action to a named character only when the evidence "
        "explicitly names that character or the declaration is unambiguous in "
        "context. If attribution is uncertain, use neutral wording such as "
        "'the group', 'a character', or omit the actor.\n"
        "- If multiple supported facts describe the same event but disagree "
        "about the actor, preserve the event and neutralize the actor instead "
        "of choosing one named character.\n"
        "- Never invent a character attribution merely to make the summary more "
        "specific.\n\n"
    )


def _evidence_strictness_policy_block() -> str:
    """Return rules that prevent extrapolation and unsupported invention."""
    return (
        "Evidence strictness policy:\n"
        "- Do not extrapolate beyond the transcript, scene facts, Facts JSON, "
        "and user-provided context.\n"
        "- Do not invent events, motives, outcomes, items, locations, resources, "
        "damage states, or relationships to make the summary smoother.\n"
        "- Do not fill gaps from genre expectations, module knowledge, previous "
        "sessions, or likely D&D mechanics unless current-session evidence "
        "explicitly supports the claim.\n"
        "- Treat weak ASR, jokes, table chatter, corrections, and interrupted "
        "sentences as insufficient evidence for specific claims.\n"
        "- If you are not sure that something happened, either omit it or state "
        "the uncertainty explicitly; never present a guess as fact.\n"
        "- Prefer fewer high-confidence claims over a richer but speculative "
        "narrative.\n\n"
    )


COMBAT_SUMMARIZATION_POLICY = """Combat summarization policy (critical):
- Do not narrate combats blow-by-blow. In "Résumé express", state who was fought and the outcome in one short sentence whenever possible.
- Mention individual attacks, spells, damage, positioning, or enemy kills only when they materially change what players must remember before the next session (e.g. a player character KO/unconscious, a lasting wound, a key NPC death, a forced retreat, or a major tactical setback).
- Omit routine enemy deaths, missed attacks, support buffs, poison duration bookkeeping, and mid-fight enemy traits unless they persist after the fight.
- Exception: always mention a player character KO or unconsciousness.
Example — do NOT write:
**Combat au temple circulaire.** Le groupe affronte des blâmes goule-like dans une salle circulaire éclairée par des ouvertures dans les murs. Garath entre au combat, frappe à mains nues puis se replie ; Ilùvatar pose un Sanctuaire sur Garath et inflige de lourds dégâts à un adversaire, tandis qu'une attaque de lance détruit au moins une tête et que d'autres ennemis tombent, dont plusieurs consumés par le feu. un compagnon est déclarée empoisonnée pour vingt-quatre heures ; Aldrik encaisse d'importantes entailles et le groupe constate que les blâmes semblent parfois profiter des dégâts nécrotiques plutôt que d'en souffrir.
Example — write instead:
**Combat au temple circulaire.** Le groupe affronte des blâmes goule-like et finit par les vaincre.
"""


def _combat_summarization_policy_block() -> str:
    """Return combat brevity rules and good/bad examples for summary prompts."""
    return f"{COMBAT_SUMMARIZATION_POLICY}\n\n"


def _prior_context_block(prior_context_text: str | None) -> str:
    """Return the summary-composer prompt block for previous sessions."""
    if not prior_context_text or not prior_context_text.strip():
        return ""
    return (
        "Previous-session context, provided by the user for continuity. "
        "Use it only to understand campaign continuity; do not repeat it unless "
        "the supported current-session facts require it.\n"
        "--- previous sessions ---\n"
        f"{prior_context_text.strip()}\n"
        "--- end previous sessions ---\n\n"
    )


def _scene_timeline_block(scene_timeline: object | None) -> str:
    """Return a compact scene timeline prompt block."""
    payload = scene_timeline_to_payload(scene_timeline)
    if not payload:
        return ""
    return (
        "Scene timeline, generated from the current transcript. Use it as the "
        "primary narrative ordering and to avoid missing major consequences. "
        "For exact actor attribution, Facts JSON and forbidden claims are "
        "authoritative; if the timeline and facts disagree about who acted or "
        "was affected, keep the event and use neutral actor wording.\n"
        "--- scene timeline ---\n"
        f"{to_yaml(payload)}\n"
        "--- end scene timeline ---\n\n"
    )


def scene_timeline_to_payload(scene_timeline: object | None) -> list[dict[str, Any]]:
    """Serialize a scene timeline-like object for prompts."""
    if scene_timeline is None:
        return []
    scenes = getattr(scene_timeline, "scenes", None)
    if not isinstance(scenes, list):
        return []
    payload: list[dict[str, Any]] = []
    for scene in scenes:
        row = {
            "scene_id": getattr(scene, "scene_id", None),
            "title": getattr(scene, "title", ""),
            "start": getattr(scene, "start", None),
            "end": getattr(scene, "end", None),
            "summary": getattr(scene, "summary", ""),
            "key_actions": list(getattr(scene, "key_actions", []) or []),
            "state_changes": list(getattr(scene, "state_changes", []) or []),
            "continuity_impacts": list(
                getattr(scene, "continuity_impacts", []) or []
            ),
            "facts": [
                {
                    "claim": getattr(fact, "claim", ""),
                    "claim_type": (
                        getattr(getattr(fact, "claim_type", None), "value", None)
                        or str(getattr(fact, "claim_type", ""))
                    ),
                    "importance": getattr(fact, "importance", None),
                }
                for fact in list(getattr(scene, "facts", []) or [])
            ],
        }
        payload.append(row)
    return payload


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
        metadata={"source": "composer_fallback", "composer_yaml_error": True},
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
                "metadata": fact.metadata,
            },
        )
    return rows
