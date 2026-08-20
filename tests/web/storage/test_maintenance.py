from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import tara_web.app as application
from tara_web.storage.artifacts import ArtifactPolicy
from tara_web.storage.layout import StorageLayout


def test_storage_maintenance_runs_initial_cycle_and_cancels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        application,
        "_storage_maintenance_cycle",
        lambda *args, **kwargs: calls.extend(["expired", "orphans"]),
    )

    async def run() -> None:
        task = asyncio.create_task(
            application._storage_maintenance(  # noqa: SLF001 - lifecycle helper.
                StorageLayout(tmp_path), object(), ArtifactPolicy(), 0
            )
        )
        while len(calls) < 2:
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert calls == ["expired", "orphans"]
