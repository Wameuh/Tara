"""Prompts for scene-driven analysis."""

from __future__ import annotations

import json

from tara.analysis.models import MergedTranscription, TranscriptionSegment
from tara.analysis.scenes.models import SceneBoundary, SceneTranscription

BOUNDARY_SYSTEM_PROMPT = (
    "You identify narrative scenes in tabletop RPG session transcripts. "
    "Return strict JSON only. Do not include markdown fences."
)

BOUNDARY_USER_PROMPT = """Identify the narrative scenes in this tabletop RPG session.

Scene rules:
- A scene is a coherent narrative phase, location, objective, or combat phase.
- Prefer meaningful narrative boundaries over tiny turn-by-turn fragments.
- Do not force a fixed number of scenes.
- Timestamps must use the supplied segment times.
- Small overlaps or gaps are acceptable, but keep scenes ordered.

Return JSON:
{{
  "scenes": [
    {{
      "scene_id": 1,
      "title": "short factual title",
      "start": 0.0,
      "end": 120.0,
      "summary": "1-2 sentence summary"
    }}
  ]
}}

Transcript:
{transcript}
"""

BOUNDARY_MERGE_USER_PROMPT = """Merge these block-level scene boundaries into one
global scene timeline.

Rules:
- Preserve chronological order.
- Merge duplicate or near-duplicate scenes at block edges.
- Keep timestamps in seconds and keep scene_id sequential from 1.
- Return JSON with the same schema: {{"scenes":[...]}}.

Block scene JSON:
{block_scenes}
"""

DESCRIPTION_SYSTEM_PROMPT = (
    "You extract a rich but disciplined scene description from tabletop RPG "
    "transcript evidence. Return strict JSON only, no markdown fences."
)

DESCRIPTION_USER_PROMPT = """Describe this scene and extract scene facts for a
blackboard pipeline.

Style and content rules:
- Internal output can be in English.
- Do not quote transcript lines.
- Prioritize stakes, consequences, key actions, state changes, resources, and
  next-session continuity.
- Mention D&D mechanics only when they change story state, danger, resources, or
  continuity.
- Extract adaptive facts: enough to preserve important details, not an inventory.
- Every fact must be supported by segment ids from this scene.
- Importance 3 is default. Use importance 4 for key actions, final states,
  deaths, major resource changes, discoveries, or next-session consequences.

Return JSON:
{{
  "scene_id": 1,
  "title": "short factual title",
  "start": 0.0,
  "end": 120.0,
  "summary": "short summary",
  "description": "long scene description",
  "facts": [
    {{
      "claim": "short factual claim",
      "claim_type": "one allowed claim type",
      "confidence": "high|medium|low",
      "importance": 3,
      "is_critical": false,
      "supporting_segment_ids": [1, 2]
    }}
  ],
  "key_actions": ["..."],
  "state_changes": ["..."],
  "continuity_impacts": ["..."]
}}

Scene metadata:
{scene_metadata}

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
        transcript=format_transcription_segments(transcription),
    )


def boundary_merge_user_prompt(boundaries: list[list[SceneBoundary]]) -> str:
    """Build the prompt that merges block-level boundaries."""
    payload = [
        [scene.to_dict() for scene in block_scenes]
        for block_scenes in boundaries
    ]
    return BOUNDARY_MERGE_USER_PROMPT.format(
        block_scenes=json.dumps(payload, ensure_ascii=True, indent=2),
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
        scene_metadata=json.dumps(metadata, ensure_ascii=True, indent=2),
        transcript=format_scene_transcript(scene),
    )


def _format_segment(index: int, segment: TranscriptionSegment) -> str:
    """Render one transcript segment for boundary prompts."""
    speaker = segment.author.speaker if segment.author else "unknown"
    return (
        f"[segment_id={index} {segment.start:.2f}-{segment.end:.2f}s {speaker}] "
        f"{segment.text.strip()}"
    )


def _speaker_from_raw_segment(segment: dict) -> str:
    """Extract a speaker label from a serialized segment."""
    author = segment.get("author")
    if isinstance(author, dict):
        speaker = author.get("speaker")
        if isinstance(speaker, str) and speaker.strip():
            return speaker.strip()
    return "unknown"
