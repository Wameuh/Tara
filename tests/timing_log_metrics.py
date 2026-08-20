"""Parse Record10 Modal timing logs for post-snapshot benchmark tests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_TIMESTAMP = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})",
)
_FINISHED = re.compile(
    r"Finished transcription (\d+)/\d+: .+ \((\d+\.\d+)s on Modal\)",
)
_ACTIVE = re.compile(r"\(\d+ files, (\d+) active,")
_QUEUE = re.compile(r"still queued for .+ \((\d+)s waiting for a container\)")


@dataclass(frozen=True)
class TimingMetrics:
    """Extracted timing metrics from one Tara Modal log."""

    total_wall_time: float | None
    max_active: int
    cold_modal_min: float | None
    cold_modal_max: float | None
    warm_modal_min: float | None
    warm_modal_max: float | None
    best_handoff_gap: int | None
    worst_handoff_gap: int | None


def _parse_timestamp(line: str) -> datetime | None:
    """Parse a log timestamp prefix when present."""
    match = _TIMESTAMP.match(line)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S,%f")


def parse_timing_log(path: Path) -> TimingMetrics:
    """Extract benchmark metrics from a Tara Record10 timing log."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    transcription_start: datetime | None = None
    processing_start: datetime | None = None
    modal_times: dict[int, float] = {}
    max_active = 0
    queue_waits: list[int] = []

    for line in lines:
        timestamp = _parse_timestamp(line)
        if "Running transcription stage." in line and timestamp is not None:
            transcription_start = timestamp
        if "Running processing stage." in line and timestamp is not None:
            processing_start = timestamp

        finished = _FINISHED.search(line)
        if finished is not None:
            modal_times[int(finished.group(1))] = float(finished.group(2))

        active = _ACTIVE.search(line)
        if active is not None:
            max_active = max(max_active, int(active.group(1)))

        queued = _QUEUE.search(line)
        if queued is not None:
            queue_waits.append(int(queued.group(1)))

    total_wall_time: float | None = None
    if transcription_start is not None and processing_start is not None:
        total_wall_time = (processing_start - transcription_start).total_seconds()

    cold = [modal_times[i] for i in (1, 2, 3) if i in modal_times]
    warm = [modal_times[i] for i in (4, 5, 6) if i in modal_times]

    return TimingMetrics(
        total_wall_time=total_wall_time,
        max_active=max_active,
        cold_modal_min=min(cold) if cold else None,
        cold_modal_max=max(cold) if cold else None,
        warm_modal_min=min(warm) if warm else None,
        warm_modal_max=max(warm) if warm else None,
        best_handoff_gap=min(queue_waits) if queue_waits else None,
        worst_handoff_gap=max(queue_waits) if queue_waits else None,
    )
