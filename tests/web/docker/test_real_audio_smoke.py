from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import smoke_real_audio_compose  # noqa: E402


def test_available_docker_subnet_ignores_network_without_ipam_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        (
            subprocess.CompletedProcess(
                args=["docker", "network", "ls", "-q"],
                returncode=0,
                stdout="network-id\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["docker", "network", "inspect", "network-id"],
                returncode=0,
                stdout=json.dumps([{"IPAM": {"Config": None}}]),
                stderr="",
            ),
        )
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr(smoke_real_audio_compose.os, "getpid", lambda: 1)

    assert smoke_real_audio_compose.available_docker_subnet() == "172.30.2.0/29"
