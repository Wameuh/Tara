"""Golden, migration and hostile-input coverage for versioned YAML schemas."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tara.schemas.merged_transcription import MergedTranscriptionContent
from tara.schemas.public_result import (
    CalloutBlock,
    KeyValueBlock,
    ListBlock,
    OrderedListBlock,
    ParagraphBlock,
    PublicResult,
    TableBlock,
    _section_type,
    publish_internal_result,
    stable_section_id,
)
from tara.schemas.registry import (
    load_merged_transcription,
    load_merged_transcription_text,
    load_public_result,
    load_public_result_text,
)
from tara.yaml_utils import (
    MERGED_TRANSCRIPTION_LIMITS,
    PUBLIC_RESULT_LIMITS,
    YamlLimits,
    YamlSecurityError,
    from_yaml,
    read_json,
    to_yaml,
    validate_json_like,
)

FIXTURES = Path(__file__).with_name("fixtures")


def test_merged_golden_round_trip() -> None:
    document = load_merged_transcription(FIXTURES / "merged-current-26.0.1.yaml")
    assert (
        load_merged_transcription_text(to_yaml(document.model_dump(mode="json")))
        == document
    )


def test_public_golden_round_trip_preserves_section_and_block_order() -> None:
    document = load_public_result(FIXTURES / "public-current-26.0.1.yaml")
    assert [section.section_id for section in document.content.sections] == [
        "overview-tour",
        "quests-tour",
    ]
    assert [block.type for block in document.content.sections[0].blocks] == [
        "paragraph",
        "list",
        "orderedList",
        "keyValue",
        "table",
        "callout",
    ]
    assert (
        load_public_result_text(to_yaml(document.model_dump(mode="json"))) == document
    )


def test_legacy_merged_migrates_to_current() -> None:
    migrated = load_merged_transcription(FIXTURES / "merged-legacy-v0.yaml")
    assert migrated.schema_version == "26.0.1"
    assert migrated.text == "Bonjour"


def test_legacy_adapters_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        load_merged_transcription_text(
            "text: Bonjour\nsegments: []\nsecret_path: C:/private\n"
        )
    with pytest.raises(ValidationError):
        load_public_result_text(
            "schema_version: 1\nsummary:\n  title: Tara\n  prompt: leak\n"
        )


def test_final_v1_migrates_to_current_public_result() -> None:
    migrated = load_public_result(FIXTURES / "final-legacy-v1.yaml")
    assert migrated.schema_name == "tara.public_result"
    assert migrated.content.sections[0].section_id == "overview-tara-legacy"


@pytest.mark.parametrize(
    "payload",
    [
        "schema_version: 1\nsummary:\n  title: Tara\n  scenes: []\n  sections:\n"
        "    - title: One\n      content: Fine\n      extra: rejected\n",
        "schema_version: 1\nsummary:\n  title: Tara\n  scenes:\n    - ignored\n",
    ],
)
def test_final_v1_rejects_unsupported_nested_shapes(payload: str) -> None:
    with pytest.raises(ValidationError):
        load_public_result_text(payload)


def test_unknown_version_and_extra_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        load_public_result_text("schema_name: tara.public_result\nschema_version: 99\n")
    with pytest.raises(ValidationError):
        PublicResult.model_validate(
            {
                "schema_name": "tara.public_result",
                "schema_version": "26.0.1",
                "metadata": {},
                "content": {"title": "T", "sections": []},
                "trace_path": "C:/secret",
            }
        )
    with pytest.raises(ValidationError):
        load_merged_transcription(FIXTURES / "invalid-unknown-field.yaml")


def test_section_ids_are_safe_unique_and_stable() -> None:
    used: set[str] = set()
    assert stable_section_id("characters", "Les Heros!", used) == "characters-les-heros"
    assert (
        stable_section_id("characters", "Les Heros!", used) == "characters-les-heros-2"
    )
    assert stable_section_id("unknown", "../../C:", set()) == "generic-c"
    assert stable_section_id("characters", "Héros", set()) == "characters-heros"
    assert _section_type("resume_express", "ignored") == "overview"
    assert _section_type("final_state", "ignored") == "generic"


def test_public_projection_is_allowlisted() -> None:
    result = publish_internal_result(
        "Tara",
        [
            {
                "section_id": "combat",
                "title": "Combat",
                "content": "- Victoire\nC:/secret\n```trace```",
                "prompt": "leak",
            }
        ],
    )
    dumped = result.model_dump(mode="json")
    assert "prompt" not in str(dumped)
    assert "section_id" in dumped["content"]["sections"][0]
    assert "trace_path" not in str(dumped)
    assert "C:/secret" not in str(dumped)
    assert "```" not in str(dumped)


def test_public_projection_preserves_bounded_summary_markdown() -> None:
    markdown = "# Résumé de session\n\n## Résumé express\n\n- Le groupe avance.\n"
    result = publish_internal_result(
        "Tara",
        [{"title": "Résumé express", "content": "- Le groupe avance."}],
        summary_markdown=markdown,
    )

    assert result.content.summary_markdown == markdown


def test_public_summary_markdown_rejects_absolute_paths() -> None:
    with pytest.raises(ValueError, match="absolute path"):
        publish_internal_result(
            "Tara",
            [{"title": "Résumé", "content": "Contenu"}],
            summary_markdown="# Résumé\n\n/data/private/result.yaml",
        )


def test_public_projection_keeps_narrative_trace_and_converts_markdown() -> None:
    result = publish_internal_result(
        "Rapport C:/private/title",
        [
            {
                "section_id": "overview",
                "title": "# Titre C:/private/section",
                "content": """# Heading
> Quoted line
[Useful link](https://example.test/private)
![Map](https://example.test/map.png)
**bold** and *italic* with `code`.
* Alternative item
---
<b>Visible text</b>
<!-- hidden comment -->
Le groupe suit une trace.
```text
hidden C:/private/code
```
C:/private/content
""",
            }
        ],
    )
    section = result.content.sections[0]
    public_text = "\n".join(
        block.text for block in section.blocks if isinstance(block, ParagraphBlock)
    )
    assert result.content.title == "Rapport"
    assert section.title == "Titre"
    assert "Heading" in public_text
    assert "Quoted line" in public_text
    assert "Useful link" in public_text and "Map" in public_text
    assert "bold and italic with code." in public_text
    assert "Visible text" in public_text
    assert "Le groupe suit une trace." in public_text
    assert "hidden" not in public_text
    assert "comment" not in public_text and "<!--" not in public_text
    assert "C:/" not in public_text and "https://" not in public_text
    assert "---" not in public_text and "<b>" not in public_text
    assert any(isinstance(block, ListBlock) for block in section.blocks)


@pytest.mark.parametrize(
    "raw_markdown",
    [
        "```code```",
        "# Heading",
        "> quote",
        "[label](https://example.test)",
        "![image](https://example.test/image.png)",
        "**strong**",
        "inline `code`",
    ],
)
def test_forged_current_public_result_rejects_raw_markdown(raw_markdown: str) -> None:
    payload = {
        "schema_name": "tara.public_result",
        "schema_version": "26.0.1",
        "content": {
            "title": "Tara",
            "sections": [
                {
                    "section_id": "overview-tara",
                    "section_type": "overview",
                    "title": "Overview",
                    "blocks": [{"type": "paragraph", "text": raw_markdown}],
                }
            ],
        },
    }
    with pytest.raises(ValidationError, match="raw Markdown"):
        load_public_result_text(to_yaml(payload))


@pytest.mark.parametrize(
    "raw_markdown",
    [
        "*italic*",
        "_italic_",
        "- raw item",
        "1. raw item",
        "---",
        "<b>raw</b>",
        "</tag>",
        "<!-- hidden -->",
    ],
)
def test_forged_current_public_result_rejects_additional_markdown(
    raw_markdown: str,
) -> None:
    payload = {
        "schema_name": "tara.public_result",
        "schema_version": "26.0.1",
        "content": {
            "title": "Tara",
            "sections": [
                {
                    "section_id": "overview-tara",
                    "section_type": "overview",
                    "title": "Overview",
                    "blocks": [{"type": "paragraph", "text": raw_markdown}],
                }
            ],
        },
    }
    with pytest.raises(ValidationError, match="raw Markdown"):
        load_public_result_text(to_yaml(payload))


def test_all_public_block_contracts_are_unambiguous() -> None:
    assert ParagraphBlock(text="p").type == "paragraph"
    assert ListBlock(items=["p"]).type == "list"
    assert OrderedListBlock(items=["p"]).type == "orderedList"
    assert KeyValueBlock(entries=[{"key": "k", "value": "v"}]).entries[0].key == "k"
    assert TableBlock(headers=["h"], rows=[["v"]]).headers == ["h"]
    assert CalloutBlock(title="Note", text="v").title == "Note"


@pytest.mark.parametrize(
    "payload",
    [
        "a: 1\na: 2\n",
        "a: !!python/object/apply:os.system ['echo nope']\n",
        "a: &x [one]\nb: *x\nc: *x\nd: *x\n",
        "a: yes\n",
    ],
)
def test_hostile_yaml_is_rejected(payload: str) -> None:
    with pytest.raises(YamlSecurityError):
        from_yaml(payload, limits=YamlLimits(max_aliases=2))


@pytest.mark.parametrize(
    "filename",
    [
        "hostile-alias.yaml",
        "hostile-duplicate-key.yaml",
        "hostile-python-tag.yaml",
    ],
)
def test_hostile_fixture_is_rejected(filename: str) -> None:
    with pytest.raises(YamlSecurityError):
        from_yaml((FIXTURES / filename).read_text(encoding="utf-8"))


def test_deep_fixture_is_rejected_by_custom_depth_limit() -> None:
    with pytest.raises(YamlSecurityError, match="nesting"):
        from_yaml(
            (FIXTURES / "hostile-deep.yaml").read_text(encoding="utf-8"),
            limits=YamlLimits(max_depth=3),
        )


def test_yaml_resource_limits_are_enforced() -> None:
    with pytest.raises(YamlSecurityError, match="byte"):
        from_yaml("x: " + "a" * 20, limits=YamlLimits(max_bytes=8))
    with pytest.raises(YamlSecurityError, match="nesting"):
        from_yaml("a:\n  b:\n    c: 1\n", limits=YamlLimits(max_depth=2))
    with pytest.raises(YamlSecurityError, match="scalar"):
        from_yaml("x: abcdef", limits=YamlLimits(max_scalar_chars=3))
    with pytest.raises(YamlSecurityError, match="non-finite"):
        from_yaml("x: .nan")


def test_schema_node_profiles_and_mapping_keys_are_bounded() -> None:
    assert MERGED_TRANSCRIPTION_LIMITS.max_nodes == 1_000_000
    assert PUBLIC_RESULT_LIMITS.max_nodes == 2_000_000
    with pytest.raises(YamlSecurityError, match="node"):
        validate_json_like(
            {"outer": {"child": "value"}},
            YamlLimits(max_nodes=4),
        )


def test_merged_text_limit_matches_structural_profile() -> None:
    text = "x" * (2_000_000 + 1)
    content = MergedTranscriptionContent(text=text, segments=[])
    assert content.text == text


def test_legacy_json_uses_bounded_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "legacy.json"
    path.write_text('{"text":"","segments":[]}', encoding="utf-8")

    def fail_read_bytes(_: Path) -> bytes:
        raise AssertionError("read_bytes must not be used for legacy JSON")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)
    assert load_merged_transcription(path).text == ""


def test_current_merged_schema_is_rejected_from_json(tmp_path: Path) -> None:
    path = tmp_path / "current.json"
    path.write_text(
        '{"schema_name":"tara.merged_transcription","schema_version":"26.0.1",'
        '"content":{"text":"","segments":[]}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="current merged-transcription JSON"):
        load_merged_transcription(path)


def test_bounded_json_reader_rejects_max_bytes_plus_one(tmp_path: Path) -> None:
    path = tmp_path / "oversized.json"
    path.write_bytes(b"x" * 9)
    with pytest.raises(YamlSecurityError, match="byte"):
        read_json(path, limits=YamlLimits(max_bytes=8))


def test_publisher_rejects_oversized_source_before_projection() -> None:
    with pytest.raises(ValueError, match="maximum size"):
        publish_internal_result("Tara", [{"title": "x" * (4 * 1024 * 1024 + 1)}])
    with pytest.raises(ValueError, match="section count"):
        publish_internal_result(
            "Tara",
            ({"title": "S", "content": "C"} for _ in range(101)),
        )


def test_yaml_alias_bombs_are_rejected_before_construction() -> None:
    with pytest.raises(YamlSecurityError, match="alias"):
        from_yaml("a: &a [x]\nb: [*a, *a, *a]\n")
