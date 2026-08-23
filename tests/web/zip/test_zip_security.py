from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from tara_web.services.zip_extraction import extract_audio
from tara_web.services.zip_validation import (
    ZipPolicy,
    ZipValidationError,
    inspect_zip,
)


def _archive(path: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return path


def test_safe_nested_archive_extracts_only_audio_under_generated_names(
    tmp_path: Path,
) -> None:
    path = _archive(
        tmp_path / "safe.zip",
        [
            ("table/alice.mp3", b"mp3-data"),
            ("mj.ogg", b"ogg-data"),
            ("guest.aac", b"aac-data"),
            ("music.m4a", b"m4a-data"),
            ("note.txt", b"x"),
        ],
    )
    policy = ZipPolicy(max_compression_ratio=1_000)
    inspection = inspect_zip(path, policy)
    tracks = extract_audio(path, tmp_path / "out", inspection, policy)

    assert [track.archive_name for track in tracks] == [
        "table/alice.mp3",
        "mj.ogg",
        "guest.aac",
        "music.m4a",
    ]
    assert inspection.excluded_entries == 1
    assert all(track.physical_path.parent == tmp_path / "out" for track in tracks)
    assert all(track.physical_path.suffix == ".bin" for track in tracks)
    assert {track.physical_path.read_bytes() for track in tracks} == {
        b"mp3-data",
        b"ogg-data",
        b"aac-data",
        b"m4a-data",
    }


@pytest.mark.parametrize(
    "name",
    [
        "../escape.mp3",
        "/absolute.mp3",
        "C:/device.mp3",
        "folder\\evil.mp3",
        "folder//evil.mp3",
        "folder/CON.mp3",
        "folder/audio.mp3:stream",
        "folder./audio.mp3",
    ],
)
def test_traversal_and_platform_paths_are_rejected(tmp_path: Path, name: str) -> None:
    path = _archive(tmp_path / "bad.zip", [(name, b"audio")])
    with pytest.raises(ZipValidationError, match="zip_unsafe_path"):
        inspect_zip(path, ZipPolicy())


def test_casefold_collision_is_rejected(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "collision.zip", [("Alice.mp3", b"a"), ("alice.MP3", b"b")]
    )
    with pytest.raises(ZipValidationError, match="zip_duplicate_path"):
        inspect_zip(path, ZipPolicy())


def test_symlink_entry_is_rejected(tmp_path: Path) -> None:
    link = zipfile.ZipInfo("link.mp3")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    path = _archive(tmp_path / "link.zip", [(link, b"target")])
    with pytest.raises(ZipValidationError, match="zip_non_regular_entry"):
        inspect_zip(path, ZipPolicy())


def test_ratio_and_declared_size_limits_reject_bombs(tmp_path: Path) -> None:
    path = _archive(tmp_path / "bomb.zip", [("silence.mp3", b"0" * 100_000)])
    with pytest.raises(ZipValidationError, match="zip_compression_ratio_too_high"):
        inspect_zip(path, ZipPolicy(max_compression_ratio=2))
    with pytest.raises(ZipValidationError, match="zip_uncompressed_too_large"):
        inspect_zip(path, ZipPolicy(max_uncompressed_bytes=1024))


def test_encrypted_flag_is_rejected_without_extracting(tmp_path: Path) -> None:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("alice.mp3", b"audio")
    content = bytearray(raw.getvalue())
    # General-purpose bit flag in both local and central headers.
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        index = content.index(signature)
        content[index + offset] |= 0x01
    path = tmp_path / "encrypted.zip"
    path.write_bytes(content)
    with pytest.raises(ZipValidationError, match="zip_encrypted"):
        inspect_zip(path, ZipPolicy())


def test_empty_invalid_and_audio_free_archives_have_distinct_reasons(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    invalid = tmp_path / "invalid.zip"
    invalid.write_bytes(b"not-a-zip")
    audio_free = _archive(
        tmp_path / "audio-free.zip",
        [("recording.flac", b"audio"), ("notes.txt", b"notes")],
    )

    with pytest.raises(ZipValidationError, match="zip_empty"):
        inspect_zip(empty, ZipPolicy())
    with pytest.raises(ZipValidationError, match="zip_invalid"):
        inspect_zip(invalid, ZipPolicy())
    with pytest.raises(ZipValidationError, match="zip_no_supported_audio"):
        inspect_zip(audio_free, ZipPolicy())


def test_cancelled_extraction_removes_every_partial_output(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "cancel.zip",
        [("first.mp3", b"a"), ("second.ogg", b"b")],
    )
    policy = ZipPolicy(max_compression_ratio=1_000)
    inspection = inspect_zip(path, policy)
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    output = tmp_path / "cancelled"
    with pytest.raises(ZipValidationError, match="cancelled"):
        extract_audio(path, output, inspection, policy, cancelled=cancelled)
    assert list(output.iterdir()) == []
