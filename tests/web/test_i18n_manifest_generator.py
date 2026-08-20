from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "manifest_generator",
    Path(__file__).parents[2] / "scripts/generate_i18n_manifest.py",
)
assert SPEC and SPEC.loader
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def source(
    tmp_path: Path, required: object = ["app.title"], fr: object = {"app.title": "Tara"}
) -> Path:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "i18n_required_keys.json").write_text(
        json.dumps(required), encoding="utf-8"
    )
    (tmp_path / "fr.json").write_text(json.dumps(fr), encoding="utf-8")
    return tmp_path


def test_generate_and_non_mutating_check(tmp_path: Path) -> None:
    origin, output = source(tmp_path / "src"), tmp_path / "out.json"
    generator.generate(origin, output)
    before = output.read_bytes()
    generator.check(origin, output)
    assert output.read_bytes() == before
    (origin / "fr.json").write_text(
        '{"app.title":"Changed","other":"x"}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="stale"):
        generator.check(origin, output)
    assert output.read_bytes() == before


@pytest.mark.parametrize("required", [None, [], ["x", "x"], [1]])
def test_invalid_required_keys(tmp_path: Path, required: object) -> None:
    with pytest.raises(ValueError, match="required"):
        generator.render(source(tmp_path, required))


@pytest.mark.parametrize("catalogue", [[], {"x": ""}, {"": "x"}, {"x": 1}])
def test_invalid_catalogues(tmp_path: Path, catalogue: object) -> None:
    with pytest.raises(ValueError, match="catalogue"):
        generator.render(source(tmp_path, ["x"], catalogue))


def test_invalid_language_and_missing_french(tmp_path: Path) -> None:
    origin = source(tmp_path, ["x"], {"x": "x"})
    (origin / "bad_name.json").write_text('{"x":"x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="filename"):
        generator.render(origin)
    (origin / "bad_name.json").unlink()
    (origin / "fr.json").unlink()
    with pytest.raises(ValueError, match="French"):
        generator.render(origin)


def test_missing_or_invalid_json_files_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="required"):
        generator.render(tmp_path)
    origin = source(tmp_path / "source")
    (origin / "i18n_required_keys.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="required"):
        generator.render(origin)
    (origin / "i18n_required_keys.json").write_text('["app.title"]', encoding="utf-8")
    (origin / "fr.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="catalogue"):
        generator.render(origin)
