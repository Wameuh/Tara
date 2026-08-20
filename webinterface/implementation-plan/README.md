# Plan d'implementation de la webinterface Tara

## But

Ce dossier transforme les decisions de `../DESIGN.md` en taches implementables et verifiables. Il n'invente pas le concept visuel, le theme ou la direction artistique, qui restent un chantier separe; il contient toutefois la specification mesurable necessaire pour traduire en production les deux concepts deja approuves.

Chaque tache contient:

- un objectif et un perimetre explicites;
- ses dependances et livrables;
- les fichiers probables a creer ou modifier;
- les contrats, modeles et algorithmes attendus;
- une strategie de test;
- des criteres de validation permettant de fermer la tache.

Les chemins proposes sont la cible initiale. Ils peuvent etre ajustes pendant l'implementation si la structure reelle du depot evolue, mais les responsabilites de module et les criteres d'acceptation doivent rester respectes.

## Architecture de fichiers cible

```text
TaraRepo/
|-- src/
|   |-- tara/                 # moteur Tara et contrats metier partages
|   `-- tara_web/             # application FastAPI et orchestration web
|-- tests/
|   |-- tara/                 # tests du moteur Tara
|   `-- web/                  # tests backend, integration et securite web
|-- Dockerfile                # image multi-stage de tara-web
|-- compose.yaml              # deploiement V1 de reference
`-- webinterface/
    |-- frontend/             # application React/Vite/TypeScript
    |-- implementation-plan/  # present plan
    `-- DESIGN.md
```

## Concepts UI de reference

- [`../concept_design_1.html`](../concept_design_1.html): reference de composition et d'interaction pour le suivi d'un job.
- [`../concept_design_2.html`](../concept_design_2.html): reference de composition et d'interaction pour la consultation du resultat.
- [`../logo_animations.html`](../logo_animations.html): reference des logos statiques noir/blanc et de l'animation du D20.

Ces prototypes ne sont pas livres tels quels. La [specification visuelle 07A](07a-specifications-visuelles-concepts.md) fixe palette, typographie, espacements, dimensions, grilles, breakpoints, etats et validations. La [specification 07B](07b-logo-et-animation.md) fixe les usages du logo, son animation, ses derives web et ses budgets. Les taches 07, 08 et 15 expliquent leur traduction fonctionnelle en React. En cas de conflit, `../DESIGN.md`, l'API et les schemas Tara priment.

## Ordre des taches

| Etape | Tache | Jalon principal | Depend de |
|---:|---|---|---|
| 00 | [Contrats et garde-fous](00-contrats-et-garde-fous.md) | Base commune | - |
| 01 | [Socle backend et frontend](01-socle-projet.md) | Base commune | 00 |
| 02 | [Modele de donnees SQLite](02-modele-donnees-sqlite.md) | MVP | 00, 01 |
| 03 | [Stockage gere et artefacts](03-stockage-et-artefacts.md) | MVP | 02 |
| 04 | [Upload audio reprenable](04-upload-audio-reprenable.md) | MVP | 02, 03 |
| 05 | [Orchestration et faux runner](05-orchestration-et-faux-runner.md) | MVP | 02, 03 |
| 06 | [API, secrets, SSE et securite HTTP](06-api-secrets-sse-securite.md) | MVP | 04, 05 |
| 07 | [Frontend fonctionnel du MVP](07-frontend-mvp.md) | MVP | 06 |
| 07A | [Specifications visuelles des concepts](07a-specifications-visuelles-concepts.md) | MVP | 06, 07 |
| 07B | [Logo Tara et animation du D20](07b-logo-et-animation.md) | MVP | 01, 07, 07A |
| 08 | [Validation du MVP vertical](08-validation-mvp.md) | MVP accepte | 07, 07A, 07B |
| 09 | [Schemas YAML Tara versionnes](09-schemas-yaml-versionnes.md) | Integration reelle | 00 |
| 10 | [Runner Tara pour le web](10-runner-tara-web.md) | Integration reelle | 09 |
| 11 | [Providers, Modal et telemetrie par tentative](11-providers-modal-telemetrie.md) | Integration reelle | 10 |
| 12 | [Integration audio reelle](12-integration-audio-reelle.md) | Increment audio | 08, 10, 11 |
| 13 | [Entree merged transcription YAML](13-entree-merged-transcription.md) | Increment YAML | 09, 12 |
| 14 | [Archives ZIP audio](14-entree-zip.md) | Beta | 12 |
| 15 | [Estimation, cout et budget](15-estimation-cout-budget.md) | V1 | 11, 12 |
| 16 | [Resilience, retention et sauvegarde](16-resilience-retention-sauvegarde.md) | V1 | 12, 13, 14, 15 |
| 17 | [Deploiement Docker securise](17-deploiement-docker.md) | V1 | 01 a 16 |
| 18 | [Durcissement et livraison V1](18-durcissement-livraison-v1.md) | V1 livrable | toutes |

## Strategie de jalons

### Jalon 1 - MVP vertical

Les etapes 00 a 08 livrent le vrai stockage, SQLite, les secrets, l'upload audio, la file, REST, SSE et le frontend, mais utilisent un faux runner. Le faux runner produit un YAML public realiste, simule les etapes, les erreurs et l'annulation, et n'alimente jamais le modele d'estimation utilisateur.

### Jalon 2 - Audio Tara reel

Les etapes 09 a 12 corrigent les contrats incompatibles du moteur Tara, puis remplacent le faux runner pour le parcours audio direct. La bascule doit se faire derriere la meme interface de runner afin de ne pas modifier le contrat du frontend.

### Jalon 3 - Entrees YAML et ZIP

Les etapes 13 et 14 ajoutent les deux autres modes d'entree. Le ZIP doit etre termine avant la beta.

### Jalon 4 - V1 exploitable sous Docker

Les etapes 15 a 18 ajoutent l'estimation issue de donnees reelles, le cout approximatif, le budget global, la resilience complete, les sauvegardes, le deploiement Docker securise, les tests multi-navigateurs et le runbook. Docker Compose est obligatoire pour tout deploiement V1 partage ou expose; le lancement natif reste reserve au developpement et aux tests locaux.

## Baseline de securite commune

La securite est un critere de fin de chaque tache et non une phase reservee a la livraison. Toute implementation doit appliquer les principes suivants:

- traiter fichiers, YAML, texte, noms, headers et evenements providers comme des entrees non fiables;
- appliquer validation positive, limites de taille, profondeur, duree, concurrence et temps CPU avant le traitement couteux;
- refuser par defaut une action, un etat, un type de fichier, un hote ou une origine qui n'est pas explicitement autorise;
- isoler les jobs et workers avec le minimum de fichiers, secrets, permissions et acces reseau necessaires;
- ne jamais inclure secret, contenu utilisateur, prompt, chemin absolu ou reponse provider brute dans logs, metriques, erreurs publiques ou evenements SSE;
- proteger toute mutation par autorisation, idempotence et controle de revision lorsque le cycle de vie peut etre concurrence;
- utiliser des bibliotheques maintenues pour cryptographie, parsing, archives et protocoles; ne pas inventer d'algorithme cryptographique;
- tester les chemins d'echec, entrees malveillantes, epuisement de ressources, courses, redemarrages et corruption;
- analyser les dependances et images, corriger les vulnerabilites critiques/elevees avant livraison et documenter les risques residuels;
- conserver une tracabilite technique expurgee et un identifiant de correlation pour permettre l'investigation sans exposer les donnees.

Chaque plan precise ensuite les menaces et controles propres au composant. La validation d'une tache doit inclure au moins un test negatif ou abusif pour chaque controle de securite annonce.

## Regles d'execution

- Une tache n'est fermee que lorsque ses tests et criteres d'acceptation sont satisfaits.
- Toute evolution du schema SQLite passe par une migration versionnee.
- Toute evolution incompatible de l'API exige une nouvelle version majeure de route.
- Les contrats YAML publics appartiennent a Tara, pas a `tara_web`.
- Le processus principal est seul proprietaire des mutations SQLite.
- Les workers ne recoivent que des chemins geres, des snapshots immuables et des secrets techniques strictement necessaires.
- Aucun log, chemin absolu, prompt ou artefact interne ne doit atteindre l'utilisateur.
- Aucun service applicatif autre que le reverse proxy ne publie de port hote dans le deploiement Docker de production.
- Aucun conteneur ne monte le socket Docker, la racine de l'hote, le depot ou le repertoire personnel de l'operateur.
- Les decisions nouvelles ou contradictions produit doivent etre ajoutees a `../DESIGN.md` avant implementation.

## Definition de fin globale

La V1 est terminee lorsque toutes les taches 00 a 18 sont validees, que les migrations neuves et de mise a niveau passent, que les E2E Chromium/Firefox/WebKit passent, que les tests de charge sans inference respectent les limites, et que les procedures Docker de demarrage, arret, sauvegarde, restauration et incident ont ete executees sur l'hote Linux cible. Les parcours de developpement et tests locaux restent verifies sous Windows et Linux.
