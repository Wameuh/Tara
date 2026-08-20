"""Tests for Modal inference deployment and Parakeet cache warming."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from modal_inference import (  # noqa: E402
    _FASTAPI_MAX_CONTAINERS,
    _MAX_CONTAINERS,
    _SCALEDOWN_WINDOW_SECONDS,
    _STARTUP_TIMEOUT_SECONDS,
    CACHE_VOLUME_NAME,
    DEFAULT_PARAKEET_MODEL,
    DEFAULT_PARAKEET_MODEL_WITH_PREFIX,
    ENABLE_GPU_SNAPSHOT,
    ENABLE_MEMORY_SNAPSHOT,
    IMAGE_ENV,
    MODAL_CLASS_NAME,
    MODAL_FUNCTION_NAME,
    MODEL_CACHE_DIR,
    PARAKEET_HF_REVISION,
    ParakeetTranscriber,
    _download_nemo_archive,
    _ensure_extracted_model_dir,
    _find_complete_extract_dir,
    _is_extracted_model_dir_complete,
    _normalize_parakeet_model_name,
    _parakeet_transcriber_cls_kwargs,
    _warm_parakeet_cache,
)


def test_modal_constants() -> None:
    """Deployment constants match the Tara Parakeet defaults."""
    assert DEFAULT_PARAKEET_MODEL == "nvidia/parakeet-tdt-0.6b-v3"
    assert DEFAULT_PARAKEET_MODEL_WITH_PREFIX == (
        "parakeet:nvidia/parakeet-tdt-0.6b-v3"
    )
    assert CACHE_VOLUME_NAME == "tara-parakeet-cache"
    assert MODEL_CACHE_DIR == "/model-cache"
    assert _SCALEDOWN_WINDOW_SECONDS == 10
    assert _MAX_CONTAINERS == 3
    assert _FASTAPI_MAX_CONTAINERS == 5
    assert len(PARAKEET_HF_REVISION) == 40
    assert _STARTUP_TIMEOUT_SECONDS == 2400
    assert ENABLE_MEMORY_SNAPSHOT is True
    assert ENABLE_GPU_SNAPSHOT is True
    assert MODAL_CLASS_NAME == "ParakeetTranscriber"
    assert MODAL_FUNCTION_NAME == "transcribe_one"


def test_parakeet_transcriber_cls_is_registered() -> None:
    """ParakeetTranscriber exposes transcribe_one for Modal Cls lookup."""
    assert hasattr(ParakeetTranscriber, "transcribe_one")
    kwargs = _parakeet_transcriber_cls_kwargs()
    assert kwargs["startup_timeout"] == _STARTUP_TIMEOUT_SECONDS
    if ENABLE_GPU_SNAPSHOT:
        assert kwargs["experimental_options"] == {"enable_gpu_snapshot": True}


def test_image_env_includes_modal_cache_settings() -> None:
    """Modal image env configures HF and NeMo cache paths and HF XET."""
    assert IMAGE_ENV["HF_HOME"] == "/model-cache/huggingface"
    assert IMAGE_ENV["TARA_NEMO_EXTRACT_DIR"] == "/model-cache/nemo-extract"
    assert IMAGE_ENV["HF_XET_HIGH_PERFORMANCE"] == "1"


def test_normalize_parakeet_model_name_strips_prefix() -> None:
    """Warm helpers accept both prefixed and bare NeMo model ids."""
    assert _normalize_parakeet_model_name("parakeet:foo/bar") == "foo/bar"
    assert _normalize_parakeet_model_name("foo/bar") == "foo/bar"


def test_is_extracted_model_dir_complete_requires_artifacts(
    tmp_path: Path,
) -> None:
    """Complete extract dirs must include config-referenced artifacts."""
    extract_dir = tmp_path / "cache"
    extract_dir.mkdir()
    (extract_dir / ".tara_extract_complete").write_text("marker", encoding="utf-8")
    (extract_dir / "model_weights.ckpt").write_bytes(b"weights")
    (extract_dir / "model_config.yaml").write_text(
        "tokenizer:\n  vocab_path: nemo:tokenizer.model\n",
        encoding="utf-8",
    )
    assert _is_extracted_model_dir_complete(extract_dir) is False

    (extract_dir / "tokenizer.model").write_text("tok", encoding="utf-8")
    assert _is_extracted_model_dir_complete(extract_dir) is True


def test_find_complete_extract_dir_returns_existing_cache(tmp_path: Path) -> None:
    """Skip-if-complete locates an existing valid extract directory."""
    cache_root = tmp_path / "nemo-extract"
    extract_dir = cache_root / "nvidia_parakeet-test_7"
    extract_dir.mkdir(parents=True)
    (extract_dir / ".tara_extract_complete").write_text("marker", encoding="utf-8")
    (extract_dir / "model_weights.ckpt").write_bytes(b"weights")
    (extract_dir / "model_config.yaml").write_text("model: {}\n", encoding="utf-8")

    found = _find_complete_extract_dir(cache_root, "nvidia/parakeet-test")
    assert found == extract_dir


def test_warm_parakeet_cache_skips_when_cache_complete(tmp_path: Path) -> None:
    """Warm returns immediately when the extract directory is already complete."""
    hf_home = tmp_path / "hf"
    nemo_extract_dir = tmp_path / "nemo-extract"
    extract_dir = nemo_extract_dir / "nvidia_parakeet-test_4"
    extract_dir.mkdir(parents=True)
    (extract_dir / ".tara_extract_complete").write_text("marker", encoding="utf-8")
    (extract_dir / "model_weights.ckpt").write_bytes(b"weights")
    (extract_dir / "model_config.yaml").write_text("model: {}\n", encoding="utf-8")

    with patch("modal_inference._download_nemo_archive") as download_mock:
        result_dir, skipped = _warm_parakeet_cache(
            "parakeet:nvidia/parakeet-test",
            revision="deadbeef",
            hf_home=hf_home,
            nemo_extract_dir=nemo_extract_dir,
        )

    assert skipped is True
    assert result_dir == extract_dir
    download_mock.assert_not_called()


def test_warm_parakeet_cache_downloads_and_extracts_on_miss(tmp_path: Path) -> None:
    """Warm downloads and extracts when no complete cache directory exists."""
    hf_home = tmp_path / "hf"
    nemo_extract_dir = tmp_path / "nemo-extract"
    model_path = tmp_path / "model.nemo"
    model_path.write_bytes(b"nemo-fixture")

    class Connector:
        def _unpack_nemo_file(self, *, path2file: str, out_folder: str) -> None:
            out = Path(out_folder)
            (out / "model_weights.ckpt").write_bytes(b"weights")
            (out / "model_config.yaml").write_text("model: {}\n", encoding="utf-8")

    connector_module = ModuleType("save_restore_connector")

    class SaveRestoreConnector:
        def __init__(self) -> None:
            self._impl = Connector()

        def _unpack_nemo_file(self, *, path2file: str, out_folder: str) -> None:
            self._impl._unpack_nemo_file(path2file=path2file, out_folder=out_folder)

    connector_module.SaveRestoreConnector = SaveRestoreConnector  # type: ignore[attr-defined]

    with patch(
        "modal_inference._download_nemo_archive",
        return_value=model_path,
    ), patch.dict(
        sys.modules,
        {"nemo.core.connectors.save_restore_connector": connector_module},
    ):
        result_dir, skipped = _warm_parakeet_cache(
            "nvidia/parakeet-test",
            revision=PARAKEET_HF_REVISION,
            hf_home=hf_home,
            nemo_extract_dir=nemo_extract_dir,
        )

    assert skipped is False
    assert _is_extracted_model_dir_complete(result_dir)


def test_download_nemo_archive_uses_snapshot_download(tmp_path: Path) -> None:
    """HF snapshot download is the primary warm download path."""
    hf_home = tmp_path / "hf"
    nemo_file = hf_home / "nvidia_parakeet-test" / "model.nemo"
    nemo_file.parent.mkdir(parents=True)
    nemo_file.write_bytes(b"nemo")

    fake_hub = ModuleType("huggingface_hub")

    def fake_snapshot_download(**kwargs: Any) -> str:
        assert kwargs["repo_id"] == "nvidia/parakeet-test"
        assert kwargs["revision"] == "abc123"
        return str(nemo_file.parent)

    fake_hub.snapshot_download = fake_snapshot_download  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"huggingface_hub": fake_hub}):
        result = _download_nemo_archive(
            "nvidia/parakeet-test",
            revision="abc123",
            hf_home=hf_home,
        )

    assert result == nemo_file


def test_ensure_extracted_model_dir_reextracts_incomplete_cache(
    tmp_path: Path,
) -> None:
    """Warm re-extracts when an existing cache directory is incomplete."""
    cache_root = tmp_path / "cache"
    model_path = tmp_path / "model.nemo"
    model_path.write_bytes(b"fixture")
    extract_dir = cache_root / f"nvidia_parakeet-test_{model_path.stat().st_size}"
    extract_dir.mkdir(parents=True)
    (extract_dir / ".tara_extract_complete").write_text("stale", encoding="utf-8")
    (extract_dir / "model_weights.ckpt").write_bytes(b"stale")
    (extract_dir / "model_config.yaml").write_text(
        "tokenizer:\n  vocab_path: nemo:missing_vocab.txt\n",
        encoding="utf-8",
    )
    unpack_calls: list[tuple[str, str]] = []

    class Connector:
        def _unpack_nemo_file(self, *, path2file: str, out_folder: str) -> None:
            unpack_calls.append((path2file, out_folder))
            out = Path(out_folder)
            (out / "model_weights.ckpt").write_bytes(b"fresh")
            (out / "model_config.yaml").write_text(
                "tokenizer:\n  vocab_path: nemo:missing_vocab.txt\n",
                encoding="utf-8",
            )
            (out / "missing_vocab.txt").write_text("tok\n", encoding="utf-8")

    result = _ensure_extracted_model_dir(
        model_name="nvidia/parakeet-test",
        model_path=model_path,
        nemo_extract_dir=cache_root,
        connector=Connector(),
    )

    assert result == extract_dir
    assert (extract_dir / "missing_vocab.txt").exists()
    assert len(unpack_calls) == 1
