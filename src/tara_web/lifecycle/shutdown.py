"""Controlled process shutdown orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tara_web.db.connection import ConnectionFactory
from tara_web.orchestration.scheduler import Scheduler
from tara_web.storage.backup import (
    BackupResult,
    cleanup_expired_backups,
    create_backup,
)
from tara_web.storage.layout import StorageLayout

from .drain import DrainResult, drain_scheduler


async def controlled_drain(
    scheduler: Scheduler, *, grace_seconds: int
) -> DrainResult:
    return await drain_scheduler(scheduler, grace_seconds=grace_seconds)


def controlled_backup(
    database: ConnectionFactory,
    layout: StorageLayout,
    backups_root: Path,
    signing_key: bytes,
    *,
    now: datetime | None = None,
) -> BackupResult:
    cleanup_expired_backups(backups_root, signing_key, now=now)
    return create_backup(
        database, layout, backups_root, signing_key, now=now
    )
