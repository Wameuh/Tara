"""Tests for usage report aggregation."""

from __future__ import annotations

from tara.analysis.usage_report import UsageReport


def test_usage_report_aggregates_calls_and_percentiles() -> None:
    """Usage report should aggregate tokens and compute latency percentiles."""
    report = UsageReport()
    report.record_call(
        stage="specialist",
        purpose="analysis.specialist_extraction",
        model="Auto",
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=10,
        total_tokens=130,
        cost_usd=0.01,
        latency_ms=100,
        attempt=1,
    )
    report.record_call(
        stage="specialist",
        purpose="analysis.specialist_extraction",
        model="Auto",
        input_tokens=200,
        output_tokens=30,
        cache_read_tokens=0,
        total_tokens=230,
        cost_usd=0.02,
        latency_ms=300,
        attempt=1,
    )
    rows = report.records()
    assert len(rows) == 1
    assert rows[0].calls == 2
    assert rows[0].total_tokens == 360
    assert rows[0].latency_p50_ms == 200.0
    assert rows[0].latency_p95_ms == 290.0
    assert rows[0].latency_total_ms == 400
    summary = report.summary()
    assert summary["total_llm_latency_ms"] == 400


def test_usage_report_is_thread_safe_enough_for_parallel_recording() -> None:
    """Concurrent record_call invocations should not lose counts."""
    from concurrent.futures import ThreadPoolExecutor

    report = UsageReport()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(
                report.record_call,
                stage="specialist",
                purpose="analysis.specialist_extraction",
                model="Auto",
                input_tokens=1,
                output_tokens=1,
                cache_read_tokens=0,
                total_tokens=2,
                cost_usd=0.0,
                latency_ms=10,
                attempt=1,
            )
            for _ in range(20)
        ]
        for future in futures:
            future.result()
    assert report.summary()["total_calls"] == 20
