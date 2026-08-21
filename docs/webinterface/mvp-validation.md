# Validation MVP web - etape 08

Date: 2026-07-17. Statut: **signee comme base de regression du MVP**.

## Preuves executees

- `uv run pytest tests/web --basetemp .pytest-task08-final2 -q`: **232 passed, 8 skipped**. Les skips sont lies aux primitives non disponibles sur Windows; un avertissement de deprecation Starlette/httpx reste non bloquant.
- `npm.cmd run check`: lint, **41 tests Vitest**, TypeScript, build Vite, budget bundle, OpenAPI et manifestes i18n reussis.
- `npx playwright test e2e/concepts e2e/scenarios --project=chromium`: **14 passed** sans mise a jour des baselines.
- `E2E_BASE_URL=http://localhost:8080 npx playwright test e2e/real`: **1 passed** en 5,5 s.
- `docker compose up --build -d`: image reconstruite et services sains. Le build execute aussi lint, Vitest, TypeScript, Vite, bundle, OpenAPI et i18n.
- Image MVP: `tara-web@sha256:513e29f05124e70e64fb49dc2546f2fcd4bc2a723a56c9e574dc044c8c49cf5b`.
- La configuration rendue par `docker compose config` confirme: proxy seul sur `127.0.0.1:8080`; `tara-web` sans port publie, utilisateur `10001:10001`, lecture seule, `cap_drop: ALL`, `no-new-privileges`, volumes dedies; reseau backend interne.

## Matrice fonctionnelle

1. Parcours Compose reel: deux OGG, personne modifiee, contexte, resume, upload, validation, lancement, faux runner, resultat, rechargement et reouverture dans un nouveau contexte navigateur.
2. Upload interrompu puis repris depuis l'offset serveur, checksum binaire et finalisation.
3. File FIFO, double progression, polling et transition dynamique vers le resultat.
4. Reouverture avec secret en fragment et authentification par header uniquement.
5. Secret invalide puis rotation atomique: ancien secret abandonne et nouveau fragment actif.
6. Erreur puis `Modifier et relancer` vers une nouvelle session.
7. Timeout, relance identique unique, compteur incremente, puis relance modifiee.
8. Annulation depuis les etats en attente et en execution.
9. Expiration explicite du job et du resultat.
10. Perte SSE, polling et resynchronisation sur une revision plus recente.
11. Suivi conforme au concept 1: deux progressions, chronologie, informations, actions et transition.
12. Resultat conforme au concept 2: navigation, recherche, accordions, copie, cout et expiration.

## Charge et reprise

- Le test de charge admet 30 jobs sans provider, revendique exactement 5 jobs actifs et conserve 25 jobs en file. Il exerce 60 lectures concurrentes, 10 abonnements temps reel bornes, la limite du broker, l'annulation d'un job actif et d'un job en attente, la reprise serveur et `PRAGMA integrity_check`.
- Apres initialisation explicite du tokenizer, la latence p95 des lectures est bornee a 250 ms dans le test et chaque promotion a 1 s. Les limites 5/25 sont verifiees dans SQLite avant et apres reprise.
- La verification de release execute ce scenario sans provider dans un job dedie, avec Python et `uv` fixes, dependances verrouillees, locale UTC et `PYTHONHASHSEED=0`. Son rapport JUnit `web-load.xml` est conserve 14 jours, y compris en cas d'echec.
- La matrice crash couvre reservation, chunk durable avec suffixe non commite, finalisation/validation, promotion transactionnelle, revendication de job, progression, ecriture de resultat et suppression. Chaque cas execute la reconciliation reelle deux fois et verifie l'idempotence DB/fichiers.

## UI et accessibilite

- Les baselines `toHaveScreenshot` couvrent `1440x900`, `980x900`, `390x844` et `320x720` pour le suivi et le resultat.
- Le test a detecte puis fait corriger un debordement des boutons de recherche a 320 px.
- Les assertions couvrent absence de debordement horizontal, deux progressions accessibles, accordions, recherche, navigation, reduced motion, logo statique/anime et absence de controles interactifs imbriques.
- Les adaptations volontaires aux concepts sont: donnees dynamiques, double progression, sections ordonnees par contrat, commandes de copie separees et icones Lucide.

## Securite

- Origine, hotes, headers CSP/HSTS, rate limits, proxy, secrets absents/interdits, uploads hostiles, IPC falsifie et resultats corrompus sont couverts par `tests/web/security` et les suites API/orchestration.
- La CSP autorise uniquement `script-src 'self' 'wasm-unsafe-eval'` pour `hash-wasm` et `worker-src 'self'`; elle n'autorise pas l'evaluation JavaScript generique.
- Le scan de regression cree de vrais marqueurs prives et confirme leur absence des logs, reponses non autorisees et chemins absolus. Le secret brut et les fragments sont absents du dump SQLite; seuls les champs prives prevus conservent contexte et resumes.
- Les E2E confirment que secrets, contexte et resumes ne figurent jamais dans les URL reseau.
- `npm audit` est revenu a **0 vulnerability** apres mise a jour de Playwright vers 1.61.1.
- `uvx --python 3.13 pip-audit --requirement <export uv> --format json`: **aucune vulnerabilite connue** dans les 21 dependances verrouillees de TaraRepo. L'execution a ete explicitement autorisee, car elle peut transmettre le graphe de dependances a un service externe.

## Fonctions differees

- Runner Tara reel: remplace le faux runner lors d'une etape d'integration ulterieure.
- Entree merged transcription YAML et schemas publics YAML `26.0.1`: etape 09.
- Upload ZIP: implementation ulterieure deja documentee dans le plan.

## Decision

Le MVP est signe comme base de regression. L'integration Tara et les schemas YAML peuvent commencer sans modifier les contrats HTTP/frontend valides ici.
