# Repères du projet Tara

Tara produit des comptes rendus de sessions de jeu de rôle à partir d'audio ou
d'une transcription fusionnée. La [présentation](README.md) explique comment le
lancer; [l'architecture](ARCHITECTURE.md) décrit les composants et leurs
responsabilités.

## Composants

| Composant | Responsabilité | Emplacement |
| --- | --- | --- |
| Transcription | Convertir les pistes audio et fusionner les segments | `src/inference_server/`, `src/tara/transcription.py` |
| Analyse | Construire les scènes, preuves, faits et résumé | `src/tara/analysis/`, `src/tara/pipeline.py` |
| Contrats | Valider les entrées et publier les résultats | `src/tara/schemas/`, `docs/schemas/` |
| Interface web | Recevoir les fichiers, suivre les jobs et servir les résultats | `src/tara_web/`, `webinterface/frontend/` |
| Exploitation | Configurer, déployer, sauvegarder et surveiller | `config/`, `compose.yaml`, `docs/webinterface/` |

## Conventions

- Les chemins documentés sont relatifs à la racine de ce dépôt, sauf indication
  explicite. L'installation n'a besoin d'aucun dépôt voisin.
- `merged_transcription.yaml` est l'entrée structurée de l'analyse.
  `session_summary.md` et `session_summary.yaml` sont les résultats du moteur.
- Le contexte de campagne complète les noms et la continuité. Les événements
  du compte rendu doivent être étayés par la transcription de la session.
- Les données de session, journaux, secrets, caches et bases locales restent
  hors du contrôle de version.

## Développement

Le code et les tests Python sont sous `src/` et `tests/`. Les vérifications
courantes sont `uv run pytest` et `uv run ruff check .` après installation de
l'extra `dev`. Les vérifications propres à l'interface sont décrites dans
[webinterface/README.md](webinterface/README.md). Le processus de revue Cursor,
quand il est utilisé, est documenté dans
[.cursor/skills/tara-review/review_process/README.md](.cursor/skills/tara-review/review_process/README.md).
