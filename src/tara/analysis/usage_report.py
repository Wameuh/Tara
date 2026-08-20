"""Per-run LLM usage aggregation for Tara analysis."""
from __future__ import annotations

import csv
import statistics
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tara.yaml_utils import write_yaml


@dataclass(slots=True)
class UsageRecord:
    """Aggregated usage counters for one stage/purpose/model bucket."""

    stage: str
    purpose: str
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latencies_ms: list[int] = field(default_factory=list)
    retries: int = 0

    @property
    def latency_p50_ms(self) -> float | None:
        """Return the median latency in milliseconds."""
        if not self.latencies_ms:
            return None
        return float(statistics.median(self.latencies_ms))

    @property
    def latency_p95_ms(self) -> float | None:
        """Return the 95th percentile latency in milliseconds."""
        if not self.latencies_ms:
            return None
        if len(self.latencies_ms) == 1:
            return float(self.latencies_ms[0])
        quantiles = statistics.quantiles(self.latencies_ms, n=20, method="inclusive")
        return float(quantiles[18])

    @property
    def latency_total_ms(self) -> int:
        """Return the sum of recorded call latencies in milliseconds."""
        return sum(self.latencies_ms)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record to a JSON-compatible dictionary."""
        return {
            "stage": self.stage,
            "purpose": self.purpose,
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_total_ms": self.latency_total_ms,
            "retries": self.retries,
        }


class UsageReport:
    """Thread-safe accumulator for LLM usage across one Tara run."""

    def __init__(self) -> None:
        """Initialize an empty usage report."""
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str, str], UsageRecord] = {}

    def record_call(
        self,
        *,
        stage: str | None,
        purpose: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        total_tokens: int,
        cost_usd: float | None,
        latency_ms: int,
        attempt: int,
    ) -> None:
        """Record one LLM call into the usage report.

        Args:
            stage: High-level pipeline stage label.
            purpose: Business purpose for the call.
            model: Model name reported by the backend.
            input_tokens: Input token count.
            output_tokens: Output token count.
            cache_read_tokens: Cache-read token count.
            total_tokens: Total token count.
            cost_usd: Estimated cost in USD, if available.
            latency_ms: Request latency in milliseconds.
            attempt: Attempt number for this call.
        """
        bucket_stage = stage or "unknown"
        key = (bucket_stage, purpose, model)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                record = UsageRecord(
                    stage=bucket_stage,
                    purpose=purpose,
                    model=model,
                )
                self._records[key] = record
            record.calls += 1
            record.input_tokens += max(input_tokens, 0)
            record.output_tokens += max(output_tokens, 0)
            record.cache_read_tokens += max(cache_read_tokens, 0)
            record.total_tokens += max(total_tokens, 0)
            record.cost_usd += max(cost_usd or 0.0, 0.0)
            record.latencies_ms.append(max(latency_ms, 0))
            if attempt > 1:
                record.retries += 1

    def records(self) -> list[UsageRecord]:
        """Return aggregated records sorted by stage and purpose."""
        with self._lock:
            return sorted(
                self._records.values(),
                key=lambda item: (item.stage, item.purpose, item.model),
            )

    def summary(self) -> dict[str, Any]:
        """Return a run-level summary payload."""
        rows = self.records()
        return {
            "total_calls": sum(row.calls for row in rows),
            "total_tokens": sum(row.total_tokens for row in rows),
            "total_cost_usd": sum(row.cost_usd for row in rows),
            "total_llm_latency_ms": sum(row.latency_total_ms for row in rows),
            "records": [row.to_dict() for row in rows],
        }

    def write_yaml(self, path: Path) -> None:
        """Write the usage report as YAML."""
        write_yaml(path, self.summary())

    def write_csv(self, path: Path) -> None:
        """Write the usage report as CSV."""
        rows = self.records()
        fieldnames = [
            "stage",
            "purpose",
            "model",
            "calls",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "total_tokens",
            "cost_usd",
            "latency_p50_ms",
            "latency_p95_ms",
            "latency_total_ms",
            "retries",
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row.to_dict())
