from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from tara.config import TaraConfig
from tara.schemas.merged_transcription import (
    TranscriptionSegment,
    new_merged_transcription,
)
from tara.schemas.registry import load_merged_transcription
from tara.token_limits import count_tokens
from tara.transcription import TranscriptionDirectoryResult
from tara.web_contracts import (
    CONTRACT_VERSION,
    ErrorCode,
    EventSink,
    EventType,
    RunnerLimits,
    RunnerRequest,
    RunnerStatus,
    StageCode,
)
from tara.web_runner import (
    TaraWebRunner,
    _Cancelled,
    _InputRejected,
    _load_manifest,
    _load_server_config,
    _publish_staging_yaml,
    _read_text_bounded,
    _SequencedSink,
    _validate_token_inputs,
)
from tara.yaml_utils import write_yaml
from tara_web.services.upload_sessions import new_opaque_id


class Sink(EventSink):
    def __init__(self) -> None:
        self.events = []

    def emit(self, event: object) -> None:
        self.events.append(event)


class Token:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


def _workspace(tmp_path: Path) -> tuple[Path, str]:
    job_id = "job_0000000000001"
    workspace = tmp_path / job_id
    (workspace / "inputs").mkdir(parents=True)
    merged = new_merged_transcription(
        text="[Alice] Le groupe entre dans la taverne.",
        segments=[
            TranscriptionSegment(
                start=0,
                end=1,
                text="Le groupe entre dans la taverne.",
            )
        ],
        language="fr",
        producer="test",
    )
    write_yaml(workspace / "inputs" / "merged.yaml", merged.to_dict())
    return workspace, job_id


def _request(job_id: str, **updates: object) -> RunnerRequest:
    values: dict[str, object] = {
        "contract_version": CONTRACT_VERSION,
        "job_id": job_id,
        "attempt_number": 1,
        "input_kind": "merged_transcription",
        "merged_transcription_path": "inputs/merged.yaml",
    }
    values.update(updates)
    return RunnerRequest(**values)  # type: ignore[arg-type]


def test_frozen_server_config_ignores_later_environment_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = json.dumps(
        {
            "transcription": {
                "inference_endpoint": "https://frozen.example.test",
                "inference_auth_provider": "none",
                "parallelism": 2,
            },
            "analysis": {"parallel": False},
        }
    )
    monkeypatch.setenv("TARA_INFERENCE_ENDPOINT", "https://changed.example.test")
    monkeypatch.setenv("TARA_INFERENCE_AUTH_PROVIDER", "modal_map")
    monkeypatch.setenv("TARA_TRANSCRIPTION_PARALLELISM", "9")
    monkeypatch.setenv("TARA_ANALYSIS_PARALLEL", "true")

    config = _load_server_config(snapshot)

    assert config.transcription.inference_endpoint == "https://frozen.example.test"
    assert config.transcription.inference_auth_provider == "none"
    assert config.transcription.parallelism == 2
    assert config.analysis.parallel is False


def test_real_runner_writes_only_public_staging_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, job_id = _workspace(tmp_path)
    monkeypatch.chdir(workspace)
    sink = Sink()
    result = TaraWebRunner(TaraConfig()).run(_request(job_id), sink, Token())

    assert result.status is RunnerStatus.COMPLETED
    assert result.artifacts[0].relative_path == "work/final.yaml"
    final = (workspace / "work" / "final.yaml").read_text(encoding="utf-8")
    assert "schema_name: tara.public_result" in final
    assert str(workspace) not in final
    assert any(event.event_type is EventType.RUN_COMPLETED for event in sink.events)
    assert [event.revision for event in sink.events] == list(
        range(1, len(sink.events) + 1)
    )


def test_real_runner_rejects_token_overflow_without_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, job_id = _workspace(tmp_path)
    text = "bonjour " * 100
    (workspace / "inputs" / ("a" * 32 + ".bin")).write_text(text, encoding="utf-8")
    monkeypatch.chdir(workspace)
    request = _request(
        job_id,
        context_path="inputs/" + "a" * 32 + ".bin",
        limits=RunnerLimits(max_context_tokens=count_tokens(text) - 1),
    )
    result = TaraWebRunner(TaraConfig()).run(request, Sink(), Token())

    assert result.status is RunnerStatus.FAILED
    assert result.error_code is ErrorCode.INPUT_INVALID
    assert not (workspace / "work" / "final.yaml").exists()


def test_bounded_text_read_normalizes_universal_newlines(tmp_path: Path) -> None:
    path = tmp_path / "managed.bin"
    path.write_bytes(b"first\r\nsecond\rthird\n")
    assert _read_text_bounded(path, 100) == "first\nsecond\nthird\n"


def test_sequenced_sink_serializes_concurrent_revisions() -> None:
    sink = Sink()
    sequenced = _SequencedSink(sink)
    threads = [
        threading.Thread(
            target=sequenced.stage_started, args=(StageCode.TRANSCRIPTION,)
        )
        for _ in range(20)
    ]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert [event.revision for event in sink.events] == list(range(1, 21))


@pytest.mark.parametrize("kind", ["context", "previous", "merged"])
def test_oversized_text_is_rejected_before_tokenization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    workspace, job_id = _workspace(tmp_path)
    path = workspace / "inputs" / ("z" * 32 + ".bin")
    maximum = (
        2_000_001
        if kind == "previous"
        else 32 * 1024 * 1024 + 1
        if kind == "merged"
        else 200_001
    )
    path.write_bytes(b"x" * maximum)
    request = _request(
        job_id,
        **(
            {"context_path": "inputs/" + path.name}
            if kind == "context"
            else {"previous_summaries_path": "inputs/" + path.name}
            if kind == "previous"
            else {"merged_transcription_path": "inputs/" + path.name}
        ),
    )
    monkeypatch.setattr(
        "tara.web_runner.require_token_limit",
        lambda *_: pytest.fail("tokenizer must not be called"),
    )
    with pytest.raises(_InputRejected):
        _validate_token_inputs(request, workspace)


def test_runner_materializes_managed_text_inputs_as_tara_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, job_id = _workspace(tmp_path)
    context = workspace / "inputs" / ("b" * 32 + ".bin")
    previous = workspace / "inputs" / ("c" * 32 + ".bin")
    context.write_text("Contexte de la partie.", encoding="utf-8")
    previous.write_text("Resume precedent.", encoding="utf-8")
    monkeypatch.chdir(workspace)

    args, _ = TaraWebRunner(TaraConfig())._prepare_args(
        _request(
            job_id,
            context_path="inputs/" + context.name,
            previous_summaries_path="inputs/" + previous.name,
        ),
        workspace,
    )

    assert args.context_path == workspace / "work" / "context.txt"
    assert args.prior_context_path == workspace / "work" / "previous.md"
    assert args.context_path.read_text(encoding="utf-8") == "Contexte de la partie."
    assert args.prior_context_path.read_text(encoding="utf-8") == "Resume precedent."

    repeated, _ = TaraWebRunner(TaraConfig())._prepare_args(
        _request(
            job_id,
            context_path="inputs/" + context.name,
            previous_summaries_path="inputs/" + previous.name,
        ),
        workspace,
    )
    assert repeated.context_path == args.context_path
    assert repeated.prior_context_path == args.prior_context_path


def test_runner_reuses_existing_audio_tracks_and_text_aliases(
    tmp_path: Path,
) -> None:
    workspace, job_id = _workspace(tmp_path)
    audio = workspace / "inputs" / ("d" * 32 + ".bin")
    context = workspace / "inputs" / ("e" * 32 + ".bin")
    previous = workspace / "inputs" / ("f" * 32 + ".bin")
    audio.write_bytes(b"audio")
    context.write_text("Contexte", encoding="utf-8")
    previous.write_text("Resume", encoding="utf-8")
    manifest = {
        "version": 1,
        "job_id": job_id,
        "inputs": [
            {
                "path": "inputs/" + audio.name,
                "person": "Alice",
                "display_name": "random.mp3",
                "mime": "audio/mpeg",
                "detected_type": "mp3",
                "duration_ms": 1,
                "bytes": 5,
                "sha256": hashlib.sha256(b"audio").hexdigest(),
                "source_id": "file_000000000001",
            }
        ],
    }
    (workspace / "inputs" / "source-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    request = RunnerRequest(
        contract_version=CONTRACT_VERSION,
        job_id=job_id,
        attempt_number=1,
        input_kind="audio",
        source_manifest_path="inputs/source-manifest.json",
        context_path="inputs/" + context.name,
        previous_summaries_path="inputs/" + previous.name,
    )
    runner = TaraWebRunner(TaraConfig())
    first, _ = runner._prepare_args(request, workspace)
    second, _ = runner._prepare_args(request, workspace)
    assert first.audio_dir == second.audio_dir
    assert (workspace / "work" / "audio" / "track-0001.mp3").samefile(audio)
    assert (workspace / "work" / "context.txt").samefile(context)
    assert (workspace / "work" / "previous.md").samefile(previous)


def test_audio_runner_preserves_uploaded_source_ids_in_canonical_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, job_id = _workspace(tmp_path)
    inputs = workspace / "inputs"
    tracks = [
        ("Alice", new_opaque_id("uf"), "mp3", b"first-audio"),
        ("Benoit", new_opaque_id("uf"), "ogg", b"second-audio"),
    ]
    manifest_inputs = []
    for index, (person, source_id, detected_type, content) in enumerate(tracks):
        physical_name = f"{index + 1:032x}.bin"
        (inputs / physical_name).write_bytes(content)
        manifest_inputs.append(
            {
                "path": f"inputs/{physical_name}",
                "person": person,
                "display_name": f"private-{index}.{detected_type}",
                "mime": f"audio/{detected_type}",
                "detected_type": detected_type,
                "duration_ms": 1,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "source_id": source_id,
            }
        )
    (inputs / "source-manifest.json").write_text(
        json.dumps({"version": 1, "job_id": job_id, "inputs": manifest_inputs}),
        encoding="utf-8",
    )

    def fake_transcribe(
        audio_dir: Path,
        config: TaraConfig,
        *,
        cancellation_check: object = None,
        progress_callback: object = None,
        usage_attempt_callback: object = None,
    ) -> TranscriptionDirectoryResult:
        output_dir = audio_dir / config.transcription.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        for audio_path in sorted(audio_dir.glob("track-*")):
            write_yaml(
                output_dir / audio_path.with_suffix(".yaml").name,
                {
                    "text": f"texte {audio_path.stem}",
                    "segments": [
                        {"start": 0, "end": 1, "text": f"texte {audio_path.stem}"}
                    ],
                    "language": "fr",
                },
            )
        return TranscriptionDirectoryResult([], frozenset())

    monkeypatch.chdir(workspace)
    monkeypatch.setattr("tara.pipeline.transcribe_audio_directory", fake_transcribe)
    request = RunnerRequest(
        contract_version=CONTRACT_VERSION,
        job_id=job_id,
        attempt_number=1,
        input_kind="audio",
        source_manifest_path="inputs/source-manifest.json",
    )
    result = TaraWebRunner(TaraConfig()).run(request, Sink(), Token())
    merged = load_merged_transcription(
        workspace
        / "work"
        / "audio"
        / TaraConfig().transcription.output_dir
        / TaraConfig().processing.output_filename
    )
    authors = [segment.author for segment in merged.segments]
    assert result.status is RunnerStatus.COMPLETED
    assert {(author.speaker, author.source_file) for author in authors if author} == {
        (person, source_id) for person, source_id, _, _ in tracks
    }
    assert all(author is not None for author in authors)
    physical_names = {item["path"].split("/")[-1] for item in manifest_inputs}
    display_names = {item["display_name"] for item in manifest_inputs}
    assert all(
        "track-" not in author.source_file
        and author.source_file not in physical_names
        and author.source_file not in display_names
        for author in authors
        if author is not None
    )


@pytest.mark.parametrize(
    "source_id,accepted",
    [
        (new_opaque_id("uf"), True),
        ("file_000000000001", True),
        ("too short", False),
        ("uf_/slash_000000000", False),
        ("uf_\u0430" + "a" * 20, False),
        ("u" * 129, False),
    ],
)
def test_manifest_source_id_is_opaque_ascii_and_compatible_with_uploads(
    tmp_path: Path, source_id: str, accepted: bool
) -> None:
    workspace, job_id = _workspace(tmp_path)
    payload = {
        "version": 1,
        "job_id": job_id,
        "inputs": [
            {
                "path": "inputs/" + "a" * 32 + ".bin",
                "person": "Alice",
                "display_name": "alice.mp3",
                "mime": "audio/mpeg",
                "detected_type": "mp3",
                "duration_ms": 1,
                "bytes": 1,
                "sha256": "b" * 64,
                "source_id": source_id,
            }
        ],
    }
    path = workspace / "inputs" / "source-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    if accepted:
        assert _load_manifest(path, job_id)["inputs"][0]["source_id"] == source_id
    else:
        with pytest.raises(_InputRejected):
            _load_manifest(path, job_id)


@pytest.mark.parametrize("kind", ["context", "previous", "merged"])
@pytest.mark.parametrize("delta,passes", [(1, True), (0, True), (-1, False)])
def test_runner_token_boundaries_are_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    delta: int,
    passes: bool,
) -> None:
    workspace, job_id = _workspace(tmp_path)
    text = "un texte suffisamment distinct pour compter des tokens"
    updates: dict[str, object] = {}
    if kind == "merged":
        path = workspace / "inputs" / "merged.yaml"
        count = count_tokens(load_merged_transcription(path).content.text)
        updates["limits"] = RunnerLimits(max_merged_transcription_tokens=count + delta)
    else:
        path = workspace / "inputs" / (kind[0] * 32 + ".bin")
        path.write_text(text, encoding="utf-8")
        count = count_tokens(text)
        updates[f"{kind}_path" if kind == "context" else "previous_summaries_path"] = (
            "inputs/" + path.name
        )
        updates["limits"] = RunnerLimits(
            **(
                {"max_context_tokens": count + delta}
                if kind == "context"
                else {"max_previous_summaries_tokens": count + delta}
            )
        )
    monkeypatch.chdir(workspace)
    result = TaraWebRunner(TaraConfig()).run(
        _request(job_id, **updates), Sink(), Token()
    )
    assert (result.status is RunnerStatus.COMPLETED) is passes
    assert (workspace / "work" / "final.yaml").exists() is passes


@pytest.mark.parametrize("after_rename", [False, True])
def test_publish_cancellation_never_leaves_final(
    tmp_path: Path, after_rename: bool
) -> None:
    workspace, _ = _workspace(tmp_path)
    source = workspace / "work" / "source.yaml"
    source.parent.mkdir(exist_ok=True)
    source.write_text(
        "schema_name: tara.public_result\nschema_version: 26.0.1\ncontent:\n"
        "  title: Test\n  sections:\n    - section_id: overview-test\n"
        "      section_type: overview\n      title: Test\n      blocks:\n"
        "        - type: paragraph\n          text: Test.\n",
        encoding="utf-8",
    )
    token = Token()
    hook = (lambda: setattr(token, "cancelled", True)) if not after_rename else None
    if after_rename:
        calls = 0

        def cancelled_after_rename() -> bool:
            nonlocal calls
            calls += 1
            return calls > 1

        token.is_cancelled = cancelled_after_rename  # type: ignore[method-assign]
    with pytest.raises(_Cancelled):
        _publish_staging_yaml(
            source,
            workspace / "work" / "final.yaml",
            workspace,
            token,
            before_rename=hook,
        )
    assert not (workspace / "work" / "final.yaml").exists()


def test_runner_masks_hostile_exception_from_public_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, job_id = _workspace(tmp_path)
    marker = "api-key=not-a-real-secret"
    path = "C:/private/runtime/config.yaml"

    class ExplodingAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def run(self) -> object:
            raise RuntimeError(f"{marker} {path}")

    monkeypatch.chdir(workspace)
    monkeypatch.setattr("tara.web_runner.TaraControlAgent", ExplodingAgent)
    sink = Sink()
    result = TaraWebRunner(TaraConfig()).run(_request(job_id), sink, Token())
    public = json.dumps(
        {
            "result": asdict(result),
            "events": [event.to_payload() for event in sink.events],
        },
        default=str,
    )
    assert result.status is RunnerStatus.FAILED
    assert marker not in public and path not in public


def test_runner_classifies_failure_during_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, job_id = _workspace(tmp_path)

    class TranscriptionFailureAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.sink = kwargs["event_sink"]

        def run(self) -> object:
            self.sink.stage_started(StageCode.TRANSCRIPTION)
            raise RuntimeError("provider unavailable")

    monkeypatch.chdir(workspace)
    monkeypatch.setattr("tara.web_runner.TaraControlAgent", TranscriptionFailureAgent)
    result = TaraWebRunner(TaraConfig()).run(_request(job_id), Sink(), Token())

    assert result.status is RunnerStatus.FAILED
    assert result.error_code is ErrorCode.TRANSCRIPTION_FAILED


def test_late_runner_cancellation_emits_no_completion_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, job_id = _workspace(tmp_path)
    output = workspace / "generated"
    output.mkdir()
    (output / "final.yaml").write_text(
        "schema_name: tara.public_result\nschema_version: 26.0.1\ncontent:\n"
        "  title: Test\n  sections:\n    - section_id: overview-test\n"
        "      section_type: overview\n      title: Test\n      blocks:\n"
        "        - type: paragraph\n          text: Test.\n",
        encoding="utf-8",
    )

    class Agent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def run(self) -> object:
            return SimpleNamespace(analysis_output_dir=output)

    class LateToken(Token):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def is_cancelled(self) -> bool:
            self.calls += 1
            return self.calls >= 4

    monkeypatch.chdir(workspace)
    monkeypatch.setattr("tara.web_runner.TaraControlAgent", Agent)
    sink = Sink()
    result = TaraWebRunner(TaraConfig()).run(_request(job_id), sink, LateToken())
    assert result.status is RunnerStatus.CANCELLED
    assert not (workspace / "work" / "final.yaml").exists()
    assert not any(
        event.event_type in {EventType.ARTIFACT_DECLARED, EventType.RUN_COMPLETED}
        for event in sink.events
    )


@pytest.mark.parametrize("path", ["inputs/missing.yaml"])
def test_real_runner_rejects_paths_outside_its_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    workspace, job_id = _workspace(tmp_path)
    monkeypatch.chdir(workspace)
    request = _request(job_id, merged_transcription_path=path)
    result = TaraWebRunner(TaraConfig()).run(request, Sink(), Token())

    assert result.status is RunnerStatus.FAILED
    assert result.error_code is ErrorCode.INPUT_INVALID


def test_real_runner_masks_internal_errors_and_honours_early_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, job_id = _workspace(tmp_path)
    monkeypatch.chdir(workspace)
    cancelled = TaraWebRunner(TaraConfig()).run(_request(job_id), Sink(), Token(True))

    assert cancelled.status is RunnerStatus.CANCELLED

    # A hostile YAML is not an error message channel. The public contract
    # returns only the stable code, never parser content or a local path.
    (workspace / "inputs" / "merged.yaml").write_text("!evil boom", encoding="utf-8")
    result = TaraWebRunner(TaraConfig()).run(_request(job_id), Sink(), Token())
    assert result.error_code is ErrorCode.INPUT_INVALID


def test_real_runner_factory_exposes_the_same_contract() -> None:
    from tara_web.runners.factory import create_runner

    assert isinstance(create_runner("tara"), TaraWebRunner)
    with pytest.raises(ValueError):
        create_runner("tara", scenario="failure")


def test_real_runner_refuses_a_symlink_to_an_internal_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, job_id = _workspace(tmp_path)
    alias = workspace / "inputs" / "alias.yaml"
    try:
        alias.symlink_to(workspace / "inputs" / "merged.yaml")
    except OSError:
        pytest.skip("symbolic links are unavailable on this Windows host")
    monkeypatch.chdir(workspace)
    result = TaraWebRunner(TaraConfig()).run(
        _request(job_id, merged_transcription_path="inputs/alias.yaml"), Sink(), Token()
    )
    assert result.error_code is ErrorCode.INPUT_INVALID
