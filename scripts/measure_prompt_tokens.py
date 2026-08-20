"""Measure token savings for YAML vs JSON prompt payloads."""

from __future__ import annotations

import json
from typing import Any

import tiktoken

from tara.analysis.models import AnalysisQuestion, EvidenceChunk, RetrievalQuery
from tara.yaml_utils import to_yaml


def _count_tokens(text: str, *, encoding_name: str = "cl100k_base") -> int:
    """Return token count for a text blob."""
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))


def _specialist_prompt_pair() -> tuple[str, str]:
    """Build specialist extraction prompts in JSON and YAML forms."""
    question = AnalysisQuestion(
        question_id="combat_outcomes",
        priority=4,
        retrieval_queries=[
            RetrievalQuery(query_id="combat_q1", text="combat outcomes next session"),
        ],
        responsible_agent="CombatOutcomeAgent",
        metadata={"topic": "combat_outcomes"},
    )
    chunks = [
        EvidenceChunk(
            chunk_id="chunk_0001",
            start=0.0,
            end=90.0,
            text="Garath strikes the ghoul. The party retreats wounded.",
            segment_ids=[0, 1],
            detected_entities=["Garath"],
            lexical_tags=["combat"],
            metadata={"temporal_position": 0},
        ),
        EvidenceChunk(
            chunk_id="chunk_0002",
            start=90.0,
            end=180.0,
            text="Iluvatar casts sanctuary. One enemy falls to fire.",
            segment_ids=[2, 3],
            detected_entities=["Iluvatar"],
            lexical_tags=["combat", "healing"],
            metadata={"temporal_position": 1},
        ),
    ]
    chunk_payload = [
        {
            "chunk_id": chunk.chunk_id,
            "start": chunk.start,
            "end": chunk.end,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    q_meta_json = json.dumps(question.model_dump(mode="json"), ensure_ascii=True)
    chunks_json = json.dumps(chunk_payload, ensure_ascii=True, indent=2)
    json_prompt = (
        f"Question metadata: {q_meta_json}\n\nEvidence chunks JSON:\n{chunks_json}"
    )
    yaml_prompt = (
        f"Question metadata:\n{to_yaml(question.model_dump(mode='json'))}\n\n"
        f"Evidence chunks YAML:\n{to_yaml(chunk_payload)}"
    )
    return json_prompt, yaml_prompt


def _composer_facts_pair() -> tuple[str, str]:
    """Build composer facts payloads in JSON and YAML forms."""
    facts_payload: list[dict[str, Any]] = [
        {
            "answer_id": "combat_001",
            "claim": "The party defeats the ghoul-like blights.",
            "claim_type": "combat_outcome",
            "importance": 4,
            "is_critical": True,
            "confidence": "high",
            "chunk_ids": ["chunk_0001", "chunk_0002"],
            "metadata": {"source": "llm_extraction"},
        },
        {
            "answer_id": "resource_001",
            "claim": "Support magic is largely spent.",
            "claim_type": "resource_state",
            "importance": 3,
            "is_critical": False,
            "confidence": "medium",
            "chunk_ids": ["chunk_0002"],
            "metadata": {"source": "llm_extraction"},
        },
    ]
    do_not_claim = ["GarathAgent: debug prefix"]
    json_prompt = (
        f"Forbidden claims (do not restate): "
        f"{json.dumps(do_not_claim, ensure_ascii=True)}\n\n"
        f"Facts JSON:\n{json.dumps(facts_payload, ensure_ascii=True, indent=2)}"
    )
    yaml_prompt = (
        f"Forbidden claims (do not restate):\n{to_yaml(do_not_claim)}\n\n"
        f"Facts YAML:\n{to_yaml(facts_payload)}"
    )
    return json_prompt, yaml_prompt


def main() -> None:
    """Print before/after token counts for two representative prompts."""
    pairs = (
        ("specialist_extraction", _specialist_prompt_pair()),
        ("summary_composer_facts", _composer_facts_pair()),
    )
    for label, (json_prompt, yaml_prompt) in pairs:
        json_tokens = _count_tokens(json_prompt)
        yaml_tokens = _count_tokens(yaml_prompt)
        delta = json_tokens - yaml_tokens
        pct = (delta / json_tokens * 100.0) if json_tokens else 0.0
        print(f"{label}:")
        print(f"  JSON tokens: {json_tokens}")
        print(f"  YAML tokens: {yaml_tokens}")
        print(f"  Saved: {delta} tokens ({pct:.1f}%)")


if __name__ == "__main__":
    main()
