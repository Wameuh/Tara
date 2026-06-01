"""Convert scene descriptions into blackboard evidence answers."""

from __future__ import annotations

from tara.analysis.models import EvidenceAnswer, EvidenceSupport, FactStatus
from tara.analysis.scenes.models import SceneDescription, SceneTimeline


class SceneBlackboardIngestor:
    """Create blackboard input answers from scene-derived facts."""

    def to_evidence_answers(self, timeline: SceneTimeline) -> list[EvidenceAnswer]:
        """Convert all scene facts into supported evidence answers."""
        answers: list[EvidenceAnswer] = []
        for scene in timeline.scenes:
            answers.extend(self._scene_answers(scene))
        return answers

    @staticmethod
    def _scene_answers(scene: SceneDescription) -> list[EvidenceAnswer]:
        """Convert one scene description into evidence answers."""
        answers: list[EvidenceAnswer] = []
        for index, fact in enumerate(scene.facts):
            question_id = f"scene_{scene.scene_id:03d}"
            answer_id = (
                f"{fact.claim_type.value}_scene_{scene.scene_id:03d}_{index:02d}"
            )
            support = [
                EvidenceSupport(
                    chunk_id=f"scene_{scene.scene_id:03d}",
                    start=scene.start,
                    end=scene.end,
                    segment_ids=list(fact.supporting_segment_ids),
                    metadata={
                        "source": "scene_description",
                        "scene_id": scene.scene_id,
                        "scene_title": scene.title,
                    },
                )
            ]
            answers.append(
                EvidenceAnswer(
                    answer_id=answer_id,
                    question_id=question_id,
                    claim=fact.claim,
                    status=FactStatus.SUPPORTED,
                    importance=fact.importance,
                    support=support,
                    confidence=fact.confidence,
                    claim_type=fact.claim_type,
                    is_critical=fact.is_critical or fact.importance >= 4,
                    metadata={
                        **fact.metadata,
                        "source": "scene_description",
                        "scene_id": scene.scene_id,
                        "scene_title": scene.title,
                        "scene_start": scene.start,
                        "scene_end": scene.end,
                    },
                )
            )
        return answers
