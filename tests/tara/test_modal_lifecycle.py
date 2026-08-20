"""Tests for Modal container lifecycle helpers."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from tara.modal_lifecycle import (
    _containers_for_app,
    stop_inference_containers,
    stop_tracked_containers,
)


def test_containers_for_app_filters_by_app_name() -> None:
    """Only containers referencing the inference app are returned."""
    payload = json.dumps(
        [
            {
                "container_id": "co-111",
                "app_name": "tara-parakeet-inference",
            },
            {
                "containerId": "co-222",
                "description": "other-app",
            },
            {
                "id": "co-333",
                "function_name": "tara-parakeet-inference-fastapi-app",
            },
        ],
    )
    assert _containers_for_app(payload, app_name="tara-parakeet-inference") == [
        "co-111",
        "co-333",
    ]


def test_stop_tracked_containers_stops_only_listed_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tracked shutdown stops only the provided container IDs."""
    calls: list[list[str]] = []

    def fake_runner(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    stopped = stop_tracked_containers(
        ["co-abc", "co-def", "co-abc"],
        runner=fake_runner,
    )
    assert stopped == 2
    assert [command[-1] for command in calls] == ["co-abc", "co-def"]


def test_stop_tracked_containers_uses_snapshot_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed tracked stops fall back to new containers from a snapshot diff."""
    calls: list[list[str]] = []

    def fake_runner(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-2:] == ["list", "--json"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(
                    [
                        {"container_id": "co-old", "app_name": "tara-parakeet-inference"},
                        {"container_id": "co-new", "app_name": "tara-parakeet-inference"},
                    ],
                ),
                stderr="",
            )
        if command[-1] == "co-tracked":
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout="",
                stderr="not found",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    stopped = stop_tracked_containers(
        ["co-tracked"],
        run_start_container_ids=frozenset({"co-old"}),
        runner=fake_runner,
    )
    assert stopped == 1
    assert any(command[-1] == "co-new" for command in calls)


def test_stop_inference_containers_stops_matching_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy shutdown stops all matching Modal containers."""
    calls: list[list[str]] = []

    def fake_runner(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-1] == "--json":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "container_id": "co-abc",
                            "app_name": "tara-parakeet-inference",
                        },
                    ],
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    stopped = stop_inference_containers(runner=fake_runner)
    assert stopped == 1
    assert len(calls) == 2
    assert calls[0][-2:] == ["list", "--json"]
    assert calls[1][-3:] == ["stop", "-y", "co-abc"]
