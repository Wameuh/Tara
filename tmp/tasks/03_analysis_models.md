# 03 - Modeles de donnees analyse et blackboard

## Objectif

Creer les schemas centraux du nouveau pipeline avant d'ecrire les agents.

## A recuperer de Tara

- Style dataclasses des modules `processing`, `scene_summarizer.models`, `scene_verifier.models`.
- Conventions `to_dict` / `from_dict`.
- Gestion des `Path` et serialization JSON.

## A ne pas recuperer tel quel

- `SceneDescription` comme pivot du pipeline.
- `Scene` comme structure centrale obligatoire.
- Claims critiques extraits depuis de la prose generee.

## Modeles cibles

Creer au minimum:

- `EvidenceChunk`
- `RetrievalQuery`
- `RetrievedEvidence`
- `AnalysisQuestion`
- `AnalysisPlan`
- `EvidenceAnswer`
- `BlackboardFact`
- `Conflict`
- `ArbitrationDecision`
- `SummaryDraft`
- `SummarySection`
- `AuditFinding`
- `FinalSummary`

## Taches

1. Definir les enums:
   - `FactStatus`: supported, partial, rejected, uncertain;
   - `Confidence`: high, medium, low;
   - `ConflictSeverity`: minor, major, critical;
   - `ClaimType`: chronology, combat_outcome, character_state, quest_continuity, resource_state, final_state.
2. Definir les schemas JSON stables.
3. Ajouter validation minimale:
   - un fact `supported` doit avoir au moins un support;
   - un final state doit avoir un timestamp;
   - un claim critique (`is_critical` ou `importance >= 4`) doit avoir
     `claim_type`.
4. Prevoir `metadata` extensible dans les modeles.
5. Ajouter tests de serialization/deserialization.

## Criteres de validation

- Les agents peuvent echanger des objets sans dictionnaires ad hoc.
- Les fichiers JSON de sortie sont lisibles et stables.
- Les invariants critiques sont testes.

## Implementation Status

Status: Completed

Notes:

- `src/tara/analysis/models.py` defines the central Pydantic schemas for merged
  transcriptions, evidence chunks, retrieval queries/results, analysis plans,
  specialist answers, blackboard facts, conflicts, arbitration decisions,
  summary drafts, audit findings, and final summaries.
- Enums are implemented for `FactStatus`, `Confidence`, `ConflictSeverity`, and
  `ClaimType`.
- All models expose stable `to_dict`, `from_dict`, `to_json`, and `from_json`
  helpers through the shared base model.
- `MergedTranscription` preserves unknown metadata from existing processing
  outputs while validating the canonical `text` and `segments` fields.
- Critical invariants are validated: supported answers/facts require support,
  final-state facts require timestamps, answers/facts require `claim_type` when
  `is_critical` is `true` or `importance >= 4`, conflicts require at least two
  answers, and summary draft/final sections require `supporting_answer_ids`.
- `metadata`, `filters`, and output schema extension fields are restricted to
  JSON-compatible values.
- `MergedTranscription` preserves unknown top-level processing metadata for
  forward compatibility and must be treated as a sensitive private runtime
  artifact because it contains full transcript text.

Validation:

- From the TaraRepo root:
  `conda activate DM; python -m pytest "tests/tara/analysis/test_models.py" "tests/tara/analysis/test_llm_runner.py"`
- From the TaraRepo root:
  `conda activate DM; python -m ruff check "src" "tests"`
