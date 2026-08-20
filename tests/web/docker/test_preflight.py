from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest

_private_writable_directory = runpy.run_path(
    str(Path(__file__).parents[3] / "scripts/docker_preflight.py")
)["_private_writable_directory"]


def test_preflight_rejects_public_or_linked_volume(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    _private_writable_directory(private)
    if os.name != "nt":
        private.chmod(0o755)
        with pytest.raises(SystemExit, match="permissions"):
            _private_writable_directory(private)
        private.chmod(0o700)
    link = tmp_path / "link"
    try:
        link.symlink_to(private, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(SystemExit, match="invalid"):
        _private_writable_directory(link)
