"""Tests for structured LLM JSON parsing helpers."""

from __future__ import annotations

from tara.analysis.models import ClaimType
from tara.analysis.scenes.models import SceneDescription, SceneFact
from tara.analysis.structured_output import claim_type_from_string, parse_typed_json
from tara.yaml_utils import to_yaml


def test_claim_type_from_string_maps_scene_aliases() -> None:
    """Scene models often emit loose claim-type labels."""
    assert claim_type_from_string("location") == ClaimType.CHRONOLOGY
    assert claim_type_from_string("combat") == ClaimType.COMBAT_OUTCOME
    assert claim_type_from_string("state_change") == ClaimType.CHARACTER_STATE
    assert claim_type_from_string("unknown_label") is None


def test_scene_fact_coerces_loose_claim_types() -> None:
    """Scene facts should accept common alias strings before validation."""
    fact = SceneFact.model_validate(
        {
            "claim": "Garath is hit by an arrow for seven damage.",
            "claim_type": "combat",
            "confidence": "high",
            "importance": 4,
            "is_critical": False,
            "supporting_segment_ids": [6],
        }
    )
    assert fact.claim_type == ClaimType.COMBAT_OUTCOME


def test_scene_description_coerces_structured_action_lists() -> None:
    """Scene lists should accept object rows emitted by the LLM."""
    payload = {
        "scene_id": 2,
        "title": "Cube room",
        "start": 100.0,
        "end": 200.0,
        "summary": "Party explores a cube room.",
        "description": "Detailed scene description.",
        "facts": [],
        "key_actions": [
            {
                "action": "Kaknyr casts a spell.",
                "supporting_segment_ids": [953, 973],
            }
        ],
        "state_changes": [
            {
                "change": "The cube rotates.",
                "supporting_segment_ids": [1006],
            }
        ],
        "continuity_impacts": [
            {
                "impact": "The party remains trapped.",
                "supporting_segment_ids": [1069],
            }
        ],
    }
    scene = SceneDescription.model_validate(payload)
    assert scene.key_actions == ["Kaknyr casts a spell."]
    assert scene.state_changes == ["The cube rotates."]
    assert scene.continuity_impacts == ["The party remains trapped."]


def test_scene_description_parses_invalid_claim_type_aliases() -> None:
    """Ambush scene JSON with alias claim types should validate without repair."""
    payload = {
        "scene_id": 3,
        "title": "Ambush on the fallen tree",
        "start": 3600.0,
        "end": 5200.0,
        "summary": "Ambush on a tree bridge.",
        "description": "Crossing and combat on a fallen tree.",
        "facts": [
            {"claim": "River location.", "claim_type": "location"},
            {"claim": "Tree bridge.", "claim_type": "terrain"},
            {"claim": "Mystic step.", "claim_type": "action"},
            {"claim": "Bolt hit.", "claim_type": "combat", "is_critical": True},
            {
                "claim": "Poisoned.",
                "claim_type": "state_change",
                "is_critical": True,
            },
            {"claim": "Tabaxi shooter.", "claim_type": "entity"},
            {"claim": "Acrobatics checks.", "claim_type": "mechanics"},
            {"claim": "Fall in river.", "claim_type": "action"},
            {"claim": "Arrow damage.", "claim_type": "combat"},
            {"claim": "Elevated cover.", "claim_type": "tactics"},
            {"claim": "Rescue aid.", "claim_type": "action"},
        ],
        "key_actions": ["Cross the tree."],
        "state_changes": ["Kaknyr is poisoned."],
        "continuity_impacts": ["Tabaxi threat on far bank."],
    }
    scene = parse_typed_json(SceneDescription, to_yaml(payload))
    assert scene.facts[3].claim_type == ClaimType.COMBAT_OUTCOME
    assert scene.facts[4].claim_type == ClaimType.CHARACTER_STATE
    assert scene.facts[6].claim_type == ClaimType.RESOURCE_STATE
