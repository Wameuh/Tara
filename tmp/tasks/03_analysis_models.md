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
   - un claim critique doit avoir `claim_type`.
4. Prevoir `metadata` extensible dans les modeles.
5. Ajouter tests de serialization/deserialization.

## Criteres de validation

- Les agents peuvent echanger des objets sans dictionnaires ad hoc.
- Les fichiers JSON de sortie sont lisibles et stables.
- Les invariants critiques sont testes.
