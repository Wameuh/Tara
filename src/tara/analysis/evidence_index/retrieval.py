"""Local CPU evidence index over merged transcription chunks."""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from tara.analysis.models import (
    EvidenceChunk,
    JsonObject,
    MergedTranscription,
    RetrievalQuery,
    RetrievedEvidence,
    TranscriptionSegment,
)

TOKEN_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+")
ENTITY_PATTERN = re.compile(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'_-]{2,}\b")
KEYWORD_TAGS: dict[str, tuple[str, ...]] = {
    "combat": ("mort", "attaque", "ennemi", "combat", "bless", "tue", "mortel"),
    "healing": ("soin", "soigner", "potion", "guérir", "guerir", "pv"),
    "escape": ("fuite", "fuir", "sortir", "échappe", "echappe"),
    "resource": ("ressource", "or", "ration", "potion", "munition"),
    "place": ("lieu", "sanctuaire", "temple", "salle", "porte"),
    "object": ("objet", "lance", "clef", "clé", "mécanisme", "mecanisme"),
}
DEFAULT_MAX_SEGMENTS = 100_000
DEFAULT_MAX_CHUNKS = 10_000
DEFAULT_MAX_QUERY_TOKENS = 128
DEFAULT_MAX_SEGMENT_TEXT_CHARS = 20_000


class EvidenceCoverageReport(BaseModel):
    """Coverage metrics for a local evidence index."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    chunk_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    indexed_segment_count: int = Field(ge=0)
    duration_seconds: float = Field(ge=0.0)
    covered_duration_seconds: float = Field(ge=0.0)
    uncovered_ranges: list[tuple[float, float]] = Field(default_factory=list)
    empty_text_segment_ids: list[int] = Field(default_factory=list)


class EvidenceIndexMetadata(BaseModel):
    """Metadata describing an evidence index build."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    chunk_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
    target_window_seconds: float = Field(gt=0.0)
    overlap_seconds: float = Field(ge=0.0)
    created_at_unix: float = Field(ge=0.0)
    coverage: EvidenceCoverageReport
    metadata: JsonObject = Field(default_factory=dict)


class EvidenceIndex:
    """In-memory lexical evidence index over transcription chunks."""

    def __init__(
        self,
        chunks: list[EvidenceChunk],
        coverage: EvidenceCoverageReport,
        metadata: EvidenceIndexMetadata,
    ) -> None:
        """Initialize an in-memory evidence index.

        Args:
            chunks: Indexed evidence chunks.
            coverage: Coverage report for the indexed transcript.
            metadata: Build metadata for serialization and telemetry.
        """
        self.chunks = chunks
        self.coverage = coverage
        self.metadata = metadata
        self._chunk_tokens = {
            chunk.chunk_id: Counter(_tokenize(chunk.text)) for chunk in self.chunks
        }

    @classmethod
    def from_transcription(
        cls,
        transcription: MergedTranscription,
        target_window_seconds: float = 90.0,
        overlap_seconds: float = 20.0,
        metadata: JsonObject | None = None,
        max_segments: int = DEFAULT_MAX_SEGMENTS,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        max_segment_text_chars: int = DEFAULT_MAX_SEGMENT_TEXT_CHARS,
    ) -> EvidenceIndex:
        """Build an evidence index from a merged transcription.

        Args:
            transcription: Canonical merged transcription input.
            target_window_seconds: Target chunk duration.
            overlap_seconds: Overlap duration between consecutive windows.
            metadata: Optional JSON-compatible build metadata.
            max_segments: Maximum number of segments accepted for one index.
            max_chunks: Maximum number of chunks produced by one index build.
            max_segment_text_chars: Maximum text length accepted per segment.

        Returns:
            In-memory evidence index.

        Raises:
            ValueError: If the chunking configuration is invalid.
        """
        if target_window_seconds <= 0:
            raise ValueError("target_window_seconds must be greater than zero.")
        if overlap_seconds < 0:
            raise ValueError("overlap_seconds must be greater than or equal to zero.")
        if overlap_seconds >= target_window_seconds:
            raise ValueError("overlap_seconds must be smaller than target window.")
        _validate_input_bounds(
            transcription,
            max_segments=max_segments,
            max_segment_text_chars=max_segment_text_chars,
        )

        started_at = time.time()
        chunks = _build_chunks(
            transcription.segments,
            target_window_seconds=target_window_seconds,
            overlap_seconds=overlap_seconds,
            max_chunks=max_chunks,
        )
        duration = _transcription_duration(transcription)
        coverage = _coverage_report(transcription.segments, chunks, duration)
        index_metadata = EvidenceIndexMetadata(
            chunk_count=len(chunks),
            segment_count=len(transcription.segments),
            target_window_seconds=target_window_seconds,
            overlap_seconds=overlap_seconds,
            created_at_unix=started_at,
            coverage=coverage,
            metadata=metadata or {},
        )
        return cls(chunks=chunks, coverage=coverage, metadata=index_metadata)

    def retrieve(
        self,
        query: RetrievalQuery,
        limit: int | None = None,
        max_query_tokens: int = DEFAULT_MAX_QUERY_TOKENS,
    ) -> list[RetrievedEvidence]:
        """Retrieve relevant chunks using lexical overlap scoring.

        Args:
            query: Structured retrieval query.
            limit: Optional result limit. Defaults to `query.limit`.
            max_query_tokens: Maximum tokenized query length.

        Returns:
            Ranked retrieved evidence results.
        """
        query_tokens = Counter(_tokenize(query.text))
        if not query_tokens:
            return []
        if sum(query_tokens.values()) > max_query_tokens:
            raise ValueError("Retrieval query exceeds the maximum token count.")
        max_results = limit or query.limit
        scored: list[RetrievedEvidence] = []
        for chunk in self.chunks:
            if not _matches_filters(chunk, query.filters):
                continue
            score = self._score_chunk(query_tokens, chunk)
            if score <= 0:
                continue
            matched_tokens = _matched_tokens(
                query_tokens,
                self._chunk_tokens[chunk.chunk_id],
            )
            scored.append(
                RetrievedEvidence(
                    query_id=query.query_id,
                    chunk=chunk,
                    score=score,
                    source="lexical",
                    metadata={"matched_tokens": matched_tokens},
                )
            )
        scored.sort(key=lambda result: result.score, reverse=True)
        return scored[:max_results]

    def write_chunks_jsonl(self, path: Path) -> None:
        """Write evidence chunks as newline-delimited JSON.

        Args:
            path: Destination JSONL path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            for chunk in self.chunks:
                file.write(json.dumps(chunk.to_dict(), ensure_ascii=True))
                file.write("\n")

    def write_metadata_json(self, path: Path) -> None:
        """Write index metadata as JSON.

        Args:
            path: Destination JSON path.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                self.metadata.model_dump(mode="json"),
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def from_artifacts(
        cls,
        chunks_path: Path,
        metadata_path: Path,
    ) -> EvidenceIndex:
        """Load a local equivalent index from JSONL and metadata artifacts.

        Args:
            chunks_path: Path to `evidence_chunks.jsonl`.
            metadata_path: Path to `evidence_index_metadata.json`.

        Returns:
            In-memory evidence index reconstructed from local artifacts.
        """
        chunks = [
            EvidenceChunk.from_dict(json.loads(line))
            for line in chunks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        metadata = EvidenceIndexMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
        return cls(chunks=chunks, coverage=metadata.coverage, metadata=metadata)

    def _score_chunk(self, query_tokens: Counter[str], chunk: EvidenceChunk) -> float:
        """Score a chunk for a tokenized query."""
        chunk_tokens = self._chunk_tokens[chunk.chunk_id]
        overlap = sum(
            min(count, chunk_tokens[token])
            for token, count in query_tokens.items()
        )
        if overlap == 0:
            return 0.0
        normalized = overlap / max(sum(query_tokens.values()), 1)
        tag_bonus = 0.05 * len(set(query_tokens) & set(chunk.lexical_tags))
        normalized_entities = {_normalize(entity) for entity in chunk.detected_entities}
        entity_bonus = 0.05 * len(set(query_tokens) & normalized_entities)
        return normalized + tag_bonus + entity_bonus


def _build_chunks(
    segments: list[TranscriptionSegment],
    target_window_seconds: float,
    overlap_seconds: float,
    max_chunks: int,
) -> list[EvidenceChunk]:
    """Build sliding time-window chunks over transcription segments."""
    if not segments:
        return []

    max_end = max(segment.end for segment in segments)
    step = target_window_seconds - overlap_seconds
    window_start = min(segment.start for segment in segments)
    chunk_index = 0
    chunks: list[EvidenceChunk] = []
    while window_start <= max_end:
        window_end = window_start + target_window_seconds
        indexed_segments = [
            (index, segment)
            for index, segment in enumerate(segments)
            if segment.end > window_start and segment.start < window_end
        ]
        text_segments = [
            (index, segment)
            for index, segment in indexed_segments
            if segment.text.strip()
        ]
        if text_segments:
            if len(chunks) >= max_chunks:
                raise ValueError("Evidence index exceeds the maximum chunk count.")
            text = " ".join(segment.text.strip() for _, segment in text_segments)
            start = min(segment.start for _, segment in indexed_segments)
            end = max(segment.end for _, segment in indexed_segments)
            chunks.append(
                EvidenceChunk(
                    chunk_id=f"chunk_{chunk_index:04d}",
                    start=start,
                    end=end,
                    text=text,
                    segment_ids=[index for index, _ in indexed_segments],
                    detected_entities=_detect_entities(text),
                    lexical_tags=_detect_keyword_tags(text),
                    metadata={
                        "window_start": window_start,
                        "window_end": window_end,
                        "temporal_position": chunk_index,
                    },
                )
            )
            chunk_index += 1
        window_start += step
    return chunks


def _validate_input_bounds(
    transcription: MergedTranscription,
    max_segments: int,
    max_segment_text_chars: int,
) -> None:
    """Validate operational limits before building an index."""
    if len(transcription.segments) > max_segments:
        raise ValueError("Merged transcription exceeds the maximum segment count.")
    if transcription.text.strip() and not transcription.segments:
        raise ValueError("Merged transcription text requires source segments.")
    for index, segment in enumerate(transcription.segments):
        if len(segment.text) > max_segment_text_chars:
            raise ValueError(
                f"Segment {index} exceeds the maximum segment text length."
            )


def _coverage_report(
    segments: list[TranscriptionSegment],
    chunks: list[EvidenceChunk],
    duration_seconds: float,
) -> EvidenceCoverageReport:
    """Compute coverage metrics for chunks and source segments."""
    indexed_segment_ids = {
        segment_id for chunk in chunks for segment_id in chunk.segment_ids
    }
    empty_text_ids = [
        index for index, segment in enumerate(segments) if not segment.text.strip()
    ]
    covered_ranges = _merge_ranges([(chunk.start, chunk.end) for chunk in chunks])
    covered_duration = sum(end - start for start, end in covered_ranges)
    uncovered = _invert_ranges(covered_ranges, duration_seconds)
    return EvidenceCoverageReport(
        chunk_count=len(chunks),
        segment_count=len(segments),
        indexed_segment_count=len(indexed_segment_ids),
        duration_seconds=duration_seconds,
        covered_duration_seconds=covered_duration,
        uncovered_ranges=uncovered,
        empty_text_segment_ids=empty_text_ids,
    )


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping ranges."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _invert_ranges(
    ranges: list[tuple[float, float]],
    duration_seconds: float,
) -> list[tuple[float, float]]:
    """Return gaps between merged ranges over the transcript duration."""
    if duration_seconds <= 0:
        return []
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in ranges:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration_seconds:
        gaps.append((cursor, duration_seconds))
    return [(start, end) for start, end in gaps if end > start]


def _transcription_duration(transcription: MergedTranscription) -> float:
    """Return a transcription duration from metadata or segment bounds."""
    if transcription.duration is not None:
        return transcription.duration
    if not transcription.segments:
        return 0.0
    return max(segment.end for segment in transcription.segments)


def _detect_entities(text: str) -> list[str]:
    """Detect simple capitalized entities in a chunk."""
    entities = {match.group(0).strip("'_-") for match in ENTITY_PATTERN.finditer(text)}
    return sorted(entity for entity in entities if entity)


def _detect_keyword_tags(text: str) -> list[str]:
    """Detect coarse lexical tags from keyword groups."""
    normalized_tokens = set(_tokenize(text))
    tags = [
        tag
        for tag, keywords in KEYWORD_TAGS.items()
        if normalized_tokens & {_normalize(keyword) for keyword in keywords}
    ]
    return sorted(tags)


def _matches_filters(chunk: EvidenceChunk, filters: JsonObject) -> bool:
    """Return whether a chunk satisfies supported retrieval filters."""
    start_after = filters.get("start_after")
    if isinstance(start_after, int | float) and chunk.end < float(start_after):
        return False
    end_before = filters.get("end_before")
    if isinstance(end_before, int | float) and chunk.start > float(end_before):
        return False
    lexical_tags = _string_set_filter(filters.get("lexical_tags"))
    if lexical_tags and not lexical_tags.issubset(set(chunk.lexical_tags)):
        return False
    entities = _string_set_filter(filters.get("detected_entities"))
    normalized_entities = {_normalize(entity) for entity in chunk.detected_entities}
    return not entities or entities.issubset(normalized_entities)


def _string_set_filter(value: object) -> set[str]:
    """Normalize string or string-list filters into a set."""
    if isinstance(value, str):
        return {_normalize(value)}
    if isinstance(value, list):
        return {_normalize(item) for item in value if isinstance(item, str)}
    return set()


def _matched_tokens(
    query_tokens: Counter[str],
    chunk_tokens: Counter[str],
) -> list[str]:
    """Return query tokens that are present in the chunk."""
    return sorted(token for token in query_tokens if chunk_tokens[token] > 0)


def _tokenize(text: str) -> list[str]:
    """Tokenize normalized text for lexical retrieval."""
    return [_normalize(match.group(0)) for match in TOKEN_PATTERN.finditer(text)]


def _normalize(text: str) -> str:
    """Normalize text by lowercasing and removing accents."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
