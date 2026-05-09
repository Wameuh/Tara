# 05 - AnalysisPlannerAgent

## Objectif

Produire un plan d'analyse explicite avant de lancer les agents specialistes.

## A recuperer de Tara

- Contrat de sortie de `scene_summarizer/prompts.py`:
  - `Resume Express`;
  - `Impacts Pour La Suite`;
  - `Etat Final Et Ressources`.
- Mapping joueurs/personnages depuis les prompts existants.
- Gestion de l'historique precedent depuis `scene_summarizer/history_context.py` si utile.

## A ne pas recuperer tel quel

- Prompts qui demandent directement un resume final.
- Prompts qui condensent des descriptions de scenes.

## Taches

1. Definir les questions standard:
   - chronologie globale;
   - outcomes de combat;
   - etat final des personnages;
   - ressources;
   - lieux/objets/mecanismes;
   - incertitudes.
2. Creer `AnalysisPlannerAgent`.
3. Le planner peut etre:
   - deterministe au debut;
   - LLM-assisted ensuite si la session est complexe.
4. Pour chaque question, produire:
   - `question_id`;
   - priorite;
   - queries de retrieval;
   - agent specialiste responsable;
   - schema attendu;
   - risk level.
5. Sauvegarder `analysis_plan.json`.

## Criteres de validation

- Le plan contient toutes les questions necessaires au resume final.
- Chaque question est assignable a un agent.
- Les queries de retrieval sont inspectables.
- Aucun resume final n'est genere a cette etape.

## Implementation Status

Status: Completed

Notes:

- `AnalysisPlannerAgent` in `src/tara/analysis/agents.py` creates deterministic
  standard questions for chronology, combat outcomes, character state, quest
  continuity, resources, and uncertainty.
- Each `AnalysisQuestion` includes retrieval queries, priority, responsible
  specialist, expected claim type metadata, and risk level.
- Audit feedback count is stored in plan metadata so later LLM-assisted planning
  can use the same loop contract.

Validation:

- From the TaraRepo root:
  `conda activate DM; python -m pytest "tests/tara/analysis"`
- From the TaraRepo root:
  `conda activate DM; python -m ruff check "src" "tests"`
