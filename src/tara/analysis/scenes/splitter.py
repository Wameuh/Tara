"""Scene transcript splitter."""

from __future__ import annotations

from pathlib import Path

from tara.analysis.models import MergedTranscription, TranscriptionSegment
from tara.analysis.scenes.cache import stable_json_hash, write_json
from tara.analysis.scenes.models import SceneBoundary, SceneTranscription


class SceneSplitter:
    """Split merged transcription segments into per-scene JSON artifacts."""

    def __init__(
        self,
        *,
        scenes_dir: Path,
        output_prefix: str = "scene_",
    ) -> None:
        """Initialize the splitter."""
        self._scenes_dir = scenes_dir
        self._output_prefix = output_prefix

    def split(
        self,
        transcription: MergedTranscription,
        boundaries: list[SceneBoundary],
    ) -> list[SceneTranscription]:
        """Write scene JSON files and return their in-memory representation."""
        self._scenes_dir.mkdir(parents=True, exist_ok=True)
        serialized_segments = [
            _segment_to_dict(index, segment)
            for index, segment in enumerate(transcription.segments)
        ]
        scenes: list[SceneTranscription] = []
        for boundary in boundaries:
            scene_segments = [
                segment
                for segment in serialized_segments
                if _overlaps(segment, boundary.start, boundary.end)
            ]
            text = "\n".join(
                _render_serialized_segment_line(segment)
                for segment in scene_segments
                if str(segment.get("text", "")).strip()
            )
            source_hash = stable_json_hash(
                {
                    "boundary": boundary.to_dict(),
                    "segments": scene_segments,
                }
            )
            filename = f"{self._output_prefix}{boundary.scene_id:03d}.json"
            output_path = self._scenes_dir / filename
            scene = SceneTranscription(
                scene_id=boundary.scene_id,
                title=boundary.title,
                start=boundary.start,
                end=boundary.end,
                summary=boundary.summary,
                text=text,
                segments=scene_segments,
                source_hash=source_hash,
                output_path=str(output_path),
                metadata={"segment_count": len(scene_segments)},
            )
            write_json(output_path, scene.to_dict())
            scenes.append(scene)
        return scenes


def _segment_to_dict(index: int, segment: TranscriptionSegment) -> dict:
    """Serialize a transcript segment while preserving global segment id."""
    payload = segment.model_dump(mode="json")
    payload["segment_id"] = index
    return payload


def _overlaps(segment: dict, start: float, end: float) -> bool:
    """Return whether a serialized segment overlaps a scene range."""
    seg_start = float(segment.get("start", 0.0))
    seg_end = float(segment.get("end", seg_start))
    return seg_start < end and seg_end > start


def _render_serialized_segment_line(segment: dict) -> str:
    """Render one serialized scene segment with its source speaker."""
    speaker = "unknown"
    author = segment.get("author")
    if isinstance(author, dict):
        raw_speaker = author.get("speaker")
        if isinstance(raw_speaker, str) and raw_speaker.strip():
            speaker = raw_speaker.strip()
    return f"[{speaker}] {' '.join(str(segment.get('text', '')).split())}"
