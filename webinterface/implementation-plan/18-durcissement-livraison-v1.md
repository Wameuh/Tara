# Etape 18 - Durcissement et livraison V1

## Objectif

Verifier l'ensemble du service comme application Internet anonyme, produire son packaging et sa documentation d'exploitation, puis etablir la preuve de livraison V1.

## Dependances

Toutes les etapes precedentes, notamment l'etape 17 de deploiement Docker.

## Fichiers a creer ou modifier

- fichiers de packaging Docker Compose adaptes au serveur Linux cible et au developpement Docker Desktop;
- configuration de reverse proxy de production;
- scripts de migration, preflight, demarrage, drain et restauration;
- CI backend/frontend/E2E/securite;
- `docs/webinterface/runbook.md`;
- `docs/webinterface/api.md`;
- `docs/webinterface/release-checklist.md`;
- rapport final `docs/webinterface/v1-validation.md`.

## Matrice de tests

### Fonctionnel

Audio MP3/OGG multi-pistes, merged YAML, ZIP, contexte, resumes, file pleine, seuil disque, reprise, annulation, timeout, relance, resultat, cout et expiration.

### Backend et integration

Tests unitaires, repositories, migrations depuis chaque version supportee, contrats runner, providers simules, stockage atomique, reconciliation et sauvegarde. Executer aussi la suite Tara complete.

### Navigateurs

Chromium a chaque changement; Chromium, Firefox et WebKit avant livraison. Tester mobile et desktop, upload interrompu, SSE indisponible, rafraichissement, lien partage et changement dynamique vers le resultat.

### Charge

Sans inference reelle: uploads concurrents, 5 actifs, 25 en attente, validations, SSE/polling, annulation et nettoyage. Definir des seuils chiffres pour latence p95, memoire, erreurs et contention a partir de la machine de reference.

### Securite

- traversal, ZIP bomb, fichier polyglotte et YAML hostile;
- brute force de secret et regeneration;
- CORS, Origin, Host, proxy, CSP, cache et headers;
- idempotence et conflits de revision;
- absence de secret/PII/contenu dans logs et metriques;
- dependances et images scannees;
- volume chiffre requis pour tout deploiement non local.

## Garde de livraison securite

- Produire un modele de menace final et verifier que chaque menace possede un controle preventif, detectif ou une acceptation de risque explicite.
- Generer un SBOM Python/npm/image, verifier signatures et provenance des artefacts de build, et epingler les actions/outils CI a des versions immuables.
- Effectuer SAST, analyse de dependances, secret scanning et scan d'image; aucune vulnerabilite critique ou elevee exploitable ne peut etre ouverte.
- Executer DAST/fuzz cible sur API, upload, YAML, ZIP et SSE dans un environnement sans donnees reelles.
- Verifier que production n'active ni debug, faux runner, docs publiques, hot reload, credentials de test ou origine/hote joker.
- Documenter rotation et revocation des credentials providers, cle HMAC, secrets d'exploitation et materiel de signature de sauvegarde.
- Definir la procedure de reponse a incident: confinement, preservation des traces expurgees, rotation, notification, correction et test de non-regression.

### Accessibilite

Executer les tests automatises retenus sur tous les ecrans et etats. Documenter explicitement que cette couverture ne prouve pas une conformite WCAG AA complete.

## Packaging et exploitation

- Un seul processus applicatif proprietaire de la file locale est lance en V1.
- Docker Compose est le seul mode de deploiement V1 supporte pour un service partage ou expose; le mode natif est reserve au developpement/test.
- `tara-web` reste un conteneur applicatif unique; seul le reverse proxy publie des ports.
- Le stockage persistant, SQLite, temporaires et logs sont montes sur des volumes locaux dedies et chiffres sur l'hote non local.
- Documenter TLS/reverse proxy, limites de corps et timeouts compatibles SSE/upload.
- Exposer `live`, `ready` et metriques sans donnees utilisateur.
- Fournir les procedures de production Docker sur Linux et les parcours de developpement/test Docker Desktop sur Windows/Linux.
- Les migrations de production restent une commande explicite apres drain et sauvegarde.

## Documentation obligatoire

Le runbook couvre installation, configuration, secrets, demarrage, sante, drain, sauvegarde, migration, restauration, rotation/nettoyage, budget, circuits, saturation disque, incident provider et corruption d'artefact. Le guide API explique secret en fragment/header, idempotence, revision, SSE, statuts, erreurs et version.

Toute la documentation V1 est en francais et structuree pour traduction future.

## Validation finale

- Toutes les taches precedentes sont marquees terminees avec preuves de test.
- Aucun ecart critique/eleve de securite ou integrite n'est ouvert.
- Les trois navigateurs passent la matrice de livraison.
- Demarrage, drain, sauvegarde, migration et restauration ont ete joues sur l'hote Linux de production cible; le smoke test Docker Desktop passe sur Windows.
- Les schemas OpenAPI, TypeScript, SQLite et YAML portent leurs versions correctes.
- Le rapport final liste les risques residuels, limites connues et mesures d'exploitation.
- Le rapport inclut SBOM, resultats des scans, modele de menace et liste signee des exceptions de securite acceptees.
- La configuration Compose rendue, le digest d'image, les attestations et les resultats des tests d'isolation sont archives avec la release.

## Definition de fin

La checklist de livraison est complete, les artefacts de version sont reproductibles et l'application peut etre exploitee sans connaissance implicite du code.
