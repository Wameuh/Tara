# Etape 00 - Contrats et garde-fous

## Objectif

Figer les frontieres entre le moteur Tara, l'orchestrateur web, l'API et le frontend avant de coder les flux. Cette tache evite que le faux runner, puis Tara reel, imposent deux architectures differentes.

## Dependances

Aucune. `webinterface/DESIGN.md` est la source de verite produit.

## Travaux a realiser

1. Produire une courte note d'architecture avec les composants, leurs responsabilites et le sens des dependances.
2. Definir les machines d'etats des sessions d'upload, fichiers, validations, jobs, et artefacts.
3. Definir les contrats Python du runner, des evenements, de l'annulation et du resultat.
4. Definir le vocabulaire stable des codes d'erreur, avertissements, etapes et actions autorisees.
5. Ecrire les premieres formes OpenAPI des snapshots de session/job, sans implementer encore les routes.
6. Etablir une matrice liant chaque decision structurante du DESIGN a une tache de ce dossier.

## Fichiers a creer ou modifier

- `docs/webinterface/architecture.md`: frontieres, flux et contraintes de processus.
- `docs/webinterface/state-machines.md`: etats et transitions autorisees.
- `docs/webinterface/error-codes.md`: codes stables et traduction utilisateur.
- `src/tara/web_contracts.py`: protocoles Python partages avec Tara.
- `src/tara_web/domain/enums.py`: etats propres au service web.
- `src/tara_web/api/schemas.py`: premiers modeles Pydantic du contrat HTTP.
- `tests/tara/test_web_contracts.py`: invariants des contrats Tara.
- `tests/web/domain/test_state_machines.py`: transitions legales et illegales.

## Contrats principaux

Le protocole `TaraWebRunner` doit accepter un `RunnerRequest` immuable, un `EventSink` et un `CancellationToken`, puis retourner un `RunnerResult`. Les messages IPC doivent etre des objets serialisables sans exception Python, handle de fichier ou callback.

Types d'evenements minimaux:

- `stage_started`, `stage_progress`, `stage_completed`;
- `retry_scheduled`, `warning_raised`;
- `usage_recorded`, `artifact_declared`;
- `cancellation_acknowledged`, `run_completed`, `run_failed`.

La progression transporte l'etape, la sous-etape, le ratio de l'operation courante, le ratio global eventuel, l'estimation eventuelle et une revision monotone. Les textes visibles ne traversent pas ce contrat: seuls des codes et parametres non sensibles sont transmis.

## Algorithmes et invariants

- Modeliser chaque machine d'etat comme une table explicite `etat -> commandes -> nouvel etat`.
- Refuser une transition absente de la table plutot que la corriger implicitement.
- Calculer `allowed_actions` depuis le snapshot courant et la machine d'etat; reverifier ensuite la commande lors de son execution.
- Affecter une revision croissante a chaque mutation visible. Une revision ne doit jamais diminuer apres redemarrage.
- Distinguer statut metier terminal, code d'erreur stable et detail technique prive.

## Hors perimetre

- CSS, maquettes, theme et composition visuelle.
- Implementation des migrations et des endpoints.
- Choix fin des libelles traduits.

## Securite

- Produire un mini modele de menace couvrant attaquant Internet anonyme, utilisateur possedant un lien, fichier hostile, provider compromis et operateur mal configure.
- Marquer dans les contrats les donnees `public`, `interne`, `sensible` et `secret`; interdire qu'un champ interne ou secret existe dans un schema public.
- Definir une politique de deny-by-default pour transitions, actions, types d'evenements et codes d'artefacts inconnus.
- Ajouter des limites contractuelles aux messages IPC et payloads publics afin d'eviter allocation non bornee et denial of service.
- Documenter les frontieres de confiance et la liste minimale de secrets/accessibilites de chaque processus.

## Validation

- Les diagrammes couvrent creation, reprise, annulation, expiration, relance et redemarrage.
- Un test parametre parcourt toutes les transitions autorisees et rejette les autres.
- Le faux runner et le futur runner reel peuvent satisfaire le meme protocole sans branchement dans l'API.
- Aucun modele public ne contient de chemin serveur, prompt, log brut ou exception.
- La matrice de tracabilite ne laisse aucune decision structurante sans tache proprietaire.
- Des tests de contrat refusent champs secrets, evenements inconnus, payloads surdimensionnes et transitions non autorisees.

## Definition de fin

Contrats relus, types importables, tests unitaires verts et aucune question produit bloquante ouverte.
