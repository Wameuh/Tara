# 01 - Cartographie des modules conserves/remplaces

## Objectif

Definir clairement quels modules existants deviennent des dependances du nouveau pipeline et quels modules deviennent obsoletes.

## A recuperer de Tara

- `processing`: source officielle de `merged_transcription.json`.
- `configuration`: pattern de dataclasses/config JSON.
- `control`: orchestration globale.
- `telemetry` et `usage_reporting`: suivi des appels et couts.
- `logging`: instrumentation.
- `scene_summarizer/history_context.py` et `history_compression.py`: a recuperer si l'historique des sessions precedentes reste utile.

## A ne pas recuperer tel quel

- `scene_analyzer.agent.SceneAnalyzerAgent`
- `transcription_splitter.agent.TranscriptionSplitterAgent`
- `scene_descriptor.agent.SceneDescriptorAgent`
- `scene_verifier.agent.SceneVerifierAgent`
- prompts des anciens modules, sauf comme inspiration de contraintes de sortie.

## Nouvelle structure cible possible

```text
src/tara/analysis/
  __init__.py
  models.py
  llm_runner.py
  evidence_index/
    agent.py
    retrieval.py
    models.py
  planner/
    agent.py
    prompts.py
  specialists/
    chronology.py
    combat_outcome.py
    character_state.py
    quest_continuity.py
    uncertainty.py
  blackboard/
    controller.py
    models.py
  arbitration/
    panel.py
    policies.py
  summary/
    composer.py
    prompts.py
  audit/
    agent.py
    final_patch.py
```

## Taches

1. Decider si les anciens modules sont supprimes, deprecies ou gardes derriere feature flag.
2. Definir les imports publics du nouveau package `tara.analysis`.
3. Mapper les anciens outputs vers les nouveaux:
   - `scene_analysis.json` -> optionnel, remplace par `analysis_plan.json` + blackboard;
   - `scene_descriptions.json` -> supprime, remplace par `blackboard.json`;
   - `verification_report.json` -> remplace par `audit_report.json` + `arbitration_report.json`;
   - `session_summary.json` -> conserve comme sortie finale.
4. Mettre a jour la documentation module.

## Criteres de validation

- La nouvelle structure est documentee.
- Chaque ancien module d'analyse a un statut clair.
- Le chemin des outputs est defini.
