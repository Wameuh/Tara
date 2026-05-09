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
