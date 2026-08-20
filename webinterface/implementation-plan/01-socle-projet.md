# Etape 01 - Socle backend et frontend

## Objectif

Creer une application minimale demarrable, testable et configurable, sans encore implementer les parcours metier.

## Dependances

Etape 00.

## Fichiers a creer ou modifier

Backend:

- `src/tara_web/__init__.py` et `src/tara_web/main.py`;
- `src/tara_web/app.py`: fabrique FastAPI et cycle de vie serveur;
- `src/tara_web/config.py`: modeles Pydantic stricts;
- `src/tara_web/api/router.py`, `health.py`, `public_config.py`;
- `config/webinterface.example.yaml`;
- `pyproject.toml`: dependances et commandes necessaires.

Frontend:

- `webinterface/frontend/package.json`, `vite.config.ts`, `tsconfig.json`;
- `webinterface/frontend/src/main.tsx`, `App.tsx`;
- `webinterface/frontend/src/api/`, `features/`, `pages/`, `i18n/`;
- catalogues `fr` complets et structure d'un futur catalogue `en`;
- configuration ESLint, tests unitaires et build.
- pipeline initial d'assets locaux pour les logos Tara, sans CDN, avec dimensions explicites et noms empreintes par Vite; les details sont dans l'etape 07B.

Tests et outillage:

- `tests/web/test_app_startup.py`;
- `tests/web/test_health.py`;
- scripts de generation et verification OpenAPI/types TypeScript.
- premiere version de `Dockerfile`, `.dockerignore` et `compose.yaml`, suffisante pour lancer le socle et destinee a etre durcie a l'etape 17.

## Configuration

Le bloc `webinterface` est valide avec `extra="forbid"`. Il contient au minimum les racines de stockage et sauvegarde, les chemins SQLite, l'URL publique, les hotes/origines autorises, les limites, les delais, les langues, le nombre de workers et les options de documentation.

Le chargement suit cet ordre:

1. fichier YAML explicite;
2. variables d'environnement documentees pour les secrets et chemins d'exploitation;
3. valeurs par defaut non sensibles;
4. validation croisee des chemins, URL, langues et limites;
5. creation d'un snapshot immuable partage au runtime.

Le serveur verifie les catalogues de langues au demarrage. La langue par defaut invalide ou l'absence de langue valide bloque le demarrage; une langue secondaire incomplete est retiree de `supported_languages`.

## Cycle de vie minimal

- `live` indique uniquement que le processus repond.
- `ready` verifie configuration, acces SQLite, racine de stockage et composants indispensables.
- Le lifespan FastAPI initialise les services dans un ordre deterministe et les arrete dans l'ordre inverse.
- FastAPI sert le build React en production, avec fallback vers `index.html` uniquement hors routes `/api/`.
- OpenAPI est disponible en developpement et desactive par defaut publiquement.
- Le socle peut etre lance dans un conteneur non-root avec configuration et donnees hors image; aucun chemin de code ne suppose le repertoire courant ou un chemin Windows de l'hote.

## Securite

- Refuser le demarrage si un secret requis manque, si un chemin sensible est accessible trop largement ou si l'URL publique et les hotes autorises sont incoherents.
- Lire les secrets depuis l'environnement ou un mecanisme de secrets d'exploitation; ne jamais les placer dans YAML versionne, endpoint public ou bundle Vite.
- Lier par defaut le serveur de developpement a l'interface locale; une ecoute publique exige une configuration explicite.
- Verrouiller les versions des dependances Python et npm, executer des audits automatises et interdire les paquets frontend charges depuis un CDN.
- Desactiver debug, stack traces et documentation interactive sur l'URL publique; les erreurs de demarrage restent reservees aux logs operateur.

## Validation

- Le backend demarre avec l'exemple de configuration sur Windows et Linux.
- Une cle inconnue, un chemin invalide ou une langue par defaut incomplete fait echouer le demarrage avec un message operateur actionnable.
- `/api/v1/live`, `/api/v1/ready` et `/api/v1/config/public` ont des schemas stables.
- Le build frontend ne depend d'aucun CDN et ses assets portent une empreinte.
- Le logo statique noir est servi localement avec dimensions reservees; aucune source de galerie ou image de travail inutile n'entre dans le build runtime.
- Les types TypeScript generes depuis OpenAPI ne presentent aucun diff apres regeneration en CI.
- Les tests backend, frontend, lint et build sont verts.
- Un scan confirme qu'aucun secret de test ou variable serveur n'est inclus dans le build frontend ou la configuration publique.
- Un smoke test Compose confirme le demarrage non-root, `live`, `ready` et l'arret par `SIGTERM` sans port FastAPI publie directement.

## Definition de fin

Une commande Compose documentee lance le backend et sert une page React minimale ainsi que les endpoints techniques, sans logique de job. Le lancement natif equivalent reste disponible pour le developpement.
