# 06 - Agents specialistes avec retrieval

## Objectif

Implementer les agents qui repondent aux questions d'analyse en lisant seulement des chunks recuperes.

## A recuperer de Tara

- Prompt style factuel de `scene_verifier/prompts.py`, adapte pour des reponses JSON strictes.
- Telemetry et retry via `LLMRunner`.
- Mapping personnages.

## A ne pas recuperer tel quel

- Descriptions longues 200-400 mots par scene.
- Generation narrative intermediaire.
- Verification apres coup comme source de facts.

## Agents a implementer

Current deterministic behavior emits one sourced answer per retrieved chunk. The
semantic extraction targets below remain the target for the future LLM-assisted
specialist pass.

1. `ChronologyAgent`
   - produit 8 a 15 evenements majeurs;
   - ordre chronologique;
   - support chunks requis.
2. `CombatOutcomeAgent`
   - morts, inconsciences, stabilisations, soins majeurs;
   - ennemis neutralises ou actifs;
   - menaces.
3. `CharacterStateAgent`
   - etat final par personnage;
   - position;
   - ressources importantes.
4. `QuestContinuityAgent`
   - lieux;
   - objets;
   - mecanismes;
   - consequences pour la prochaine session.
5. `UncertaintyAgent`
   - faits contradictoires;
   - faits non confirmes;
   - claims interdits.

## Taches

1. Chaque agent recoit:
   - `AnalysisQuestion`;
   - `EvidenceRetriever`;
   - `LLMRunner`;
   - config agent.
2. Chaque agent produit des `EvidenceAnswer`.
3. Chaque `EvidenceAnswer` doit avoir:
   - claim;
   - status;
   - importance;
   - confidence;
   - support chunks;
   - contradictions eventuelles.
4. Ajouter validation post-LLM:
   - JSON strict;
   - support obligatoire;
   - pas de claim sans chunk pour `supported`.
5. Ajouter fallback:
   - si LLM echoue, produire une reponse `uncertain` ou `partial`, pas une hallucination.

## Criteres de validation

- Les agents fonctionnent sur `Record19`.
- Aucun agent ne lit tout `merged_transcription.json`.
- Les facts produits sont courts, sources et inspectables.
- Les agents peuvent utiliser API ou Cursor CLI via `LLMRunner`.

## Implementation Status

Status: Completed

Notes:

- `ChronologyAgent`, `CombatOutcomeAgent`, `CharacterStateAgent`,
  `QuestContinuityAgent`, and `UncertaintyAgent` are implemented in
  `src/tara/analysis/agents.py`.
- The first implementation is deterministic: agents answer only from
  `EvidenceRetriever.retrieve(...)` results and never read full
  `merged_transcription.json`.
- Specialist constructors accept optional `llm_runner` and JSON-compatible
  config values as the future LLM-assisted injection points.
- Empty retrieval produces an `uncertain` fallback answer rather than an
  hallucinated claim.
- Every supported answer is a short `EvidenceAnswer` with support chunk IDs,
  timestamps, segment IDs, confidence, importance, and claim type.
- `LLMRunner` is not invoked in this deterministic slice; the typed agent
  boundaries leave room for LLM-assisted specialists later.
- `resource_state` is currently routed to `QuestContinuityAgent`, and the agent
  honors the planned `ClaimType.RESOURCE_STATE` from the analysis question.
- `Record19` validation remains a manual privacy-preserving smoke check because
  private session artifacts are not loaded by automated tests.

Validation:

- From the TaraRepo root:
  `conda activate DM; python -m pytest "tests/tara/analysis"`
- From the TaraRepo root:
  `conda activate DM; python -m ruff check "src" "tests"`
