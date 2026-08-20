"""Modal GPU container lifecycle helpers for Tara transcription runs."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

MODAL_INFERENCE_APP_NAME = "tara-parakeet-inference"


def _repo_root() -> Path:
    """Return the TaraRepo root directory."""
    return Path(__file__).resolve().parents[2]


def _build_modal_command(*args: str) -> list[str]:
    """Build a Modal CLI command, preferring ``uv run`` when available in TaraRepo."""
    repo_root = _repo_root()
    uv_executable = shutil.which("uv")
    if uv_executable is not None and (repo_root / "pyproject.toml").is_file():
        return [
            uv_executable,
            "run",
            "--extra",
            "deploy",
            "modal",
            *args,
        ]
    modal_executable = shutil.which("modal")
    if modal_executable is None:
        raise FileNotFoundError(
            "Modal CLI not found. Install with `uv sync --extra deploy` "
            "or `pip install modal`.",
        )
    return [modal_executable, *args]


def _run_modal_command(
    modal_args: tuple[str, ...],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a Modal CLI command and return the completed process."""
    command = _build_modal_command(*modal_args)
    run = runner or subprocess.run
    return run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=_repo_root(),
    )


def _container_id(container: Mapping[str, Any]) -> str | None:
    """Extract a container identifier from a Modal CLI JSON record."""
    for key in ("container_id", "containerId", "id"):
        value = container.get(key)
        if value:
            return str(value)
    return None


def _containers_for_app(
    containers_payload: str,
    *,
    app_name: str,
) -> list[str]:
    """Return container IDs whose JSON payload references the given Modal app."""
    if not containers_payload.strip():
        return []

    parsed: Any = json.loads(containers_payload)
    if isinstance(parsed, Mapping):
        containers = [parsed]
    elif isinstance(parsed, list):
        containers = parsed
    else:
        return []

    container_ids: list[str] = []
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        serialized = json.dumps(container, ensure_ascii=True)
        if app_name not in serialized:
            continue
        container_id = _container_id(container)
        if container_id is not None:
            container_ids.append(container_id)
    return container_ids


def list_inference_container_ids(
    *,
    app_name: str = MODAL_INFERENCE_APP_NAME,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[str]:
    """List running Modal container IDs for the Tara inference deployment."""
    try:
        list_result = _run_modal_command(("container", "list", "--json"), runner=runner)
    except FileNotFoundError as exc:
        LOGGER.warning("Unable to list Modal containers: %s", exc)
        return []

    if list_result.returncode != 0:
        LOGGER.warning(
            "Unable to list Modal containers (exit code %s): %s",
            list_result.returncode,
            list_result.stderr.strip() or list_result.stdout.strip(),
        )
        return []

    return _containers_for_app(list_result.stdout, app_name=app_name)


def stop_tracked_containers(
    container_ids: Iterable[str],
    *,
    app_name: str = MODAL_INFERENCE_APP_NAME,
    run_start_container_ids: frozenset[str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> int:
    """Stop only the Modal containers tracked for the current Tara run.

    Args:
        container_ids: Container or task IDs collected from map results.
        app_name: Modal app name used for snapshot fallback filtering.
        run_start_container_ids: Container IDs present before the run began.
        runner: Optional subprocess runner for tests.

    Returns:
        Number of containers successfully stopped.
    """
    unique_ids = sorted({container_id.strip() for container_id in container_ids if container_id.strip()})
    if not unique_ids:
        LOGGER.warning(
            "No tracked Modal container IDs were captured for app %s; skipping shutdown.",
            app_name,
        )
        return 0

    stopped = 0
    failed_ids: list[str] = []
    for container_id in unique_ids:
        LOGGER.info("Stopping tracked Modal container %s for app %s.", container_id, app_name)
        stop_result = _run_modal_command(
            ("container", "stop", "-y", container_id),
            runner=runner,
        )
        if stop_result.returncode == 0:
            stopped += 1
            continue
        failed_ids.append(container_id)
        LOGGER.warning(
            "Unable to stop tracked Modal container %s (exit code %s): %s",
            container_id,
            stop_result.returncode,
            stop_result.stderr.strip() or stop_result.stdout.strip(),
        )

    if failed_ids and run_start_container_ids is not None:
        current_ids = set(list_inference_container_ids(app_name=app_name, runner=runner))
        fallback_ids = sorted(current_ids - set(run_start_container_ids) - set(unique_ids))
        for container_id in fallback_ids:
            LOGGER.info(
                "Stopping fallback Modal container %s discovered during run snapshot diff.",
                container_id,
            )
            stop_result = _run_modal_command(
                ("container", "stop", "-y", container_id),
                runner=runner,
            )
            if stop_result.returncode == 0:
                stopped += 1
            else:
                LOGGER.warning(
                    "Unable to stop fallback Modal container %s (exit code %s): %s",
                    container_id,
                    stop_result.returncode,
                    stop_result.stderr.strip() or stop_result.stdout.strip(),
                )

    if stopped:
        LOGGER.info("Stopped %s Modal container(s) for app %s.", stopped, app_name)
    return stopped


def stop_inference_containers(
    *,
    app_name: str = MODAL_INFERENCE_APP_NAME,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> int:
    """Stop all running Modal containers for the Tara inference deployment.

    Legacy helper retained for ``modal_proxy`` HTTP runs.
    """
    container_ids = list_inference_container_ids(app_name=app_name, runner=runner)
    if not container_ids:
        LOGGER.info("No running Modal containers found for app %s.", app_name)
        return 0

    stopped = 0
    for container_id in container_ids:
        LOGGER.info("Stopping Modal container %s for app %s.", container_id, app_name)
        stop_result = _run_modal_command(
            ("container", "stop", "-y", container_id),
            runner=runner,
        )
        if stop_result.returncode == 0:
            stopped += 1
            continue
        LOGGER.warning(
            "Unable to stop Modal container %s (exit code %s): %s",
            container_id,
            stop_result.returncode,
            stop_result.stderr.strip() or stop_result.stdout.strip(),
        )

    if stopped:
        LOGGER.info("Stopped %s Modal container(s) for app %s.", stopped, app_name)
    return stopped
