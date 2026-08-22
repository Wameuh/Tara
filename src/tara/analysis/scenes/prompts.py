"""Prompts for scene-driven analysis."""

from __future__ import annotations

from tara.analysis.models import MergedTranscription, TranscriptionSegment
from tara.analysis.scenes.models import SceneBoundary, SceneTranscription
from tara.yaml_utils import to_yaml

BOUNDARY_SYSTEM_PROMPT = (
    "You identify narrative scenes in tabletop RPG session transcripts. "
    "Return strict YAML only. Do not include markdown fences."
)

BOUNDARY_USER_PROMPT = """Identify the narrative scenes in this tabletop RPG session.

Scene rules:
- A scene is a coherent narrative phase, location, objective, or conflict phase.
- Prefer meaningful narrative boundaries over tiny turn-by-turn fragments.
- Do not force a fixed number of scenes.
- Timestamps must use the supplied segment times.
- Small overlaps or gaps are acceptable, but keep scenes ordered.
- Do not infer unseen scenes or objectives; split only from transcript evidence.

Return YAML:
scenes:
  - scene_id: 1
    title: short factual title
    start: 0.0
    end: 120.0
    summary: 1-2 sentence summary

Transcript:
{transcript}
"""

EVIDENCE_STRICTNESS_POLICY = """Evidence strictness policy:
- Do not extrapolate beyond the transcript, supplied scene metadata, and
  user-provided context.
- Do not invent events, motives, outcomes, items, locations, resources, harm or
  condition states, or relationships to make the description smoother.
- Do not fill gaps from genre expectations, published scenario knowledge,
  previous sessions, or mechanics from any game system unless current-scene
  evidence explicitly supports the claim.
- Treat weak ASR, jokes, table chatter, corrections, and interrupted sentences
  as insufficient evidence for specific claims.
- If you are not sure that something happened, either omit it or state the
  uncertainty explicitly; never present a guess as fact.
- Prefer fewer high-confidence facts over a richer but speculative narrative.
"""

SPEAKER_ATTRIBUTION_POLICY = """Speaker attribution policy:
- Audio speaker labels identify the recording track owner, not necessarily the
  in-story actor.
- The game facilitator (using the title supplied in general context) may narrate
  any NPC, adjudicate any player action, repeat a player's declaration, or joke
  out of character; do not turn facilitator first-person phrasing into a
  facilitator character action.
- A player speaker may talk about another character, quote someone, ask rules
  questions, or joke out of character. Treat player-to-character mapping as a
  weak clue only.
- ASR may mangle proper names and second-person narration; phonetic
  fragments or near-name variants are weak evidence by themselves.
- Attribute an action to a named character only when the evidence explicitly
  names that character or the declaration is unambiguous in context. If
  attribution is uncertain, use neutral wording such as "the group", "a
  character", or omit the actor.
- If multiple supported facts describe the same event but disagree about the
  actor, preserve the event and neutralize the actor instead of choosing one
  named character.
"""

BOUNDARY_MERGE_USER_PROMPT = """Merge these block-level scene boundaries into one
global scene timeline.

Rules:
- Preserve chronological order.
- Merge duplicate or near-duplicate scenes at block edges.
- Keep timestamps in seconds and keep scene_id sequential from 1.
- Return YAML with the same schema:
scenes:
  - scene_id: 1
    title: ...
    start: 0.0
    end: 120.0
    summary: ...

Block scene YAML:
{block_scenes}
"""

DESCRIPTION_SYSTEM_PROMPT = (
    "You extract a rich but disciplined scene description from tabletop RPG "
    "transcript evidence. Return strict YAML only, no markdown fences."
)

DESCRIPTION_USER_PROMPT = """Describe this scene and extract scene facts for a
blackboard pipeline.

Style and content rules:
- Internal output can be in English.
- Do not quote transcript lines.
- Prioritize stakes, consequences, key actions, state changes, resources, and
  next-session continuity.
- Interpret rules and terminology according to the game system named in general
  context. Never assume a default system.
- Mention system mechanics only when they change story state, danger, resources,
  or continuity.
- Extract adaptive facts: enough to preserve important details, not an inventory.
- Every fact must be supported by segment ids from this scene.
- Importance 3 is default. Use importance 4 for key actions, final states,
  deaths, major resource changes, discoveries, or next-session consequences.

Return YAML:
scene_id: 1
title: short factual title
start: 0.0
end: 120.0
summary: short summary
description: long scene description
facts:
  - claim: short factual claim
    claim_type: chronology|combat_outcome|character_state|quest_continuity|
      resource_state|final_state
    confidence: high|medium|low
    importance: 3
    is_critical: false
    supporting_segment_ids:
      - 1
      - 2
key_actions:
  - short action sentence
state_changes:
  - short state-change sentence
continuity_impacts:
  - short continuity sentence

Scene metadata:
{scene_metadata}

{context_block}
{evidence_policy}
{speaker_policy}
Scene transcript:
{transcript}
"""


def format_transcription_segments(transcription: MergedTranscription) -> str:
    """Render a merged transcription into timestamped prompt rows."""
    return "\n".join(
        _format_segment(index, segment)
        for index, segment in enumerate(transcription.segments)
        if segment.text.strip()
    )


def format_transcription_segments_compact(
    transcription: MergedTranscription,
    *,
    min_text_chars: int = 3,
) -> str:
    """Render a compact transcript for scene boundary detection.

    Args:
        transcription: Canonical merged transcription input.
        min_text_chars: Skip segments shorter than this threshold.

    Returns:
        Compact newline-delimited transcript rows.
    """
    rows: list[str] = []
    for index, segment in enumerate(transcription.segments):
        text = " ".join(segment.text.split())
        if len(text.strip()) < min_text_chars:
            continue
        rows.append(_format_segment_compact(index, segment))
    return "\n".join(rows)


def format_scene_transcript(scene: SceneTranscription) -> str:
    """Render a scene transcription into timestamped prompt rows."""
    rows: list[str] = []
    for local_index, raw_segment in enumerate(scene.segments):
        start = float(raw_segment.get("start", 0.0))
        end = float(raw_segment.get("end", start))
        text = str(raw_segment.get("text", "")).strip()
        if not text:
            continue
        speaker = _speaker_from_raw_segment(raw_segment)
        global_id = raw_segment.get("segment_id", local_index)
        rows.append(f"[segment_id={global_id} {start:.2f}-{end:.2f}s {speaker}] {text}")
    return "\n".join(rows)


def boundary_user_prompt(transcription: MergedTranscription) -> str:
    """Build the full-transcript boundary prompt."""
    return BOUNDARY_USER_PROMPT.format(
        transcript=format_transcription_segments_compact(transcription),
    )


def boundary_merge_user_prompt(boundaries: list[list[SceneBoundary]]) -> str:
    """Build the prompt that merges block-level boundaries."""
    payload = [
        [scene.to_dict() for scene in block_scenes] for block_scenes in boundaries
    ]
    return BOUNDARY_MERGE_USER_PROMPT.format(
        block_scenes=to_yaml(payload),
    )


def description_user_prompt(scene: SceneTranscription) -> str:
    """Build the long scene description prompt."""
    metadata = {
        "scene_id": scene.scene_id,
        "title": scene.title,
        "start": scene.start,
        "end": scene.end,
        "summary": scene.summary,
    }
    return DESCRIPTION_USER_PROMPT.format(
        scene_metadata=to_yaml(metadata),
        context_block=_context_block(None),
        evidence_policy=EVIDENCE_STRICTNESS_POLICY,
        speaker_policy=SPEAKER_ATTRIBUTION_POLICY,
        transcript=format_scene_transcript(scene),
    )


def description_user_prompt_with_context(
    scene: SceneTranscription,
    context_text: str | None,
) -> str:
    """Build the scene description prompt with optional campaign context."""
    metadata = {
        "scene_id": scene.scene_id,
        "title": scene.title,
        "start": scene.start,
        "end": scene.end,
        "summary": scene.summary,
    }
    return DESCRIPTION_USER_PROMPT.format(
        scene_metadata=to_yaml(metadata),
        context_block=_context_block(context_text),
        evidence_policy=EVIDENCE_STRICTNESS_POLICY,
        speaker_policy=SPEAKER_ATTRIBUTION_POLICY,
        transcript=format_scene_transcript(scene),
    )


def _format_segment(index: int, segment: TranscriptionSegment) -> str:
    """Render one transcript segment for boundary prompts."""
    speaker = segment.author.speaker if segment.author else "unknown"
    return (
        f"[segment_id={index} {segment.start:.2f}-{segment.end:.2f}s {speaker}] "
        f"{segment.text.strip()}"
    )


def _format_segment_compact(index: int, segment: TranscriptionSegment) -> str:
    """Render one compact transcript row for boundary prompts."""
    speaker = segment.author.speaker if segment.author else "unknown"
    text = " ".join(segment.text.split())
    start = f"{segment.start:.1f}"
    end = f"{segment.end:.1f}"
    if speaker == "unknown":
        return f"{index:04d} {start}-{end}|{text}"
    return f"{index:04d} {start}-{end} {speaker}|{text}"


def _speaker_from_raw_segment(segment: dict) -> str:
    """Extract a speaker label from a serialized segment."""
    author = segment.get("author")
    if isinstance(author, dict):
        speaker = author.get("speaker")
        if isinstance(speaker, str) and speaker.strip():
            return speaker.strip()
    return "unknown"


def _context_block(context_text: str | None) -> str:
    """Render optional user-provided campaign context for scene prompts."""
    if not context_text or not context_text.strip():
        return ""
    return (
        "General context for the game system, names, aliases, user-to-character "
        "mapping, game-facilitator identity, and optional public campaign name. "
        "Use the named system's terminology and rules; never assume another "
        "system. Use transcript evidence for events.\n"
        "--- general context ---\n"
        f"{context_text.strip()}\n"
        "--- end general context ---\n\n"
    )
