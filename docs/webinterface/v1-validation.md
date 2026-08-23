# Rapport de validation V1

Ce rapport distingue la preuve reproductible du dépôt de la décision d'une
release donnée. Les mesures locales détaillées sont dans
[Validation V1 web](mvp-validation.md) et la liste à signer pour chaque version
dans la [Checklist de livraison](release-checklist.md).

## Périmètre validé dans le dépôt

- contrats API, OpenAPI, TypeScript, SQLite et YAML versionnés ;
- entrées audio MP3/OGG/AAC/M4A, merged transcription YAML et ZIP ;
- upload reprenable, file 5+25, SSE avec repli polling, annulation, relance,
  coût, budget, expiration et rétention ;
- runner Tara réel derrière doubles provider déterministes ;
- Docker Compose TLS non-root, rootfs en lecture seule, sauvegarde authentifiée,
  restauration isolée et scans de dépendances/image ;
- matrices backend, frontend, charge et multi-navigateurs documentées dans la
  preuve du 21 août 2026.

Le commit `9f10742` ajoute en plus un parcours Compose provider-free avec deux
audios réels et Chromium. Le scénario reste volontairement ignoré dans les
quatre projets de la matrice statique, mais passe séparément contre la pile TLS
réelle. Les mesures actuelles et le digest d'image sont consignés dans
[Validation V1 web](mvp-validation.md).

## Modèle de menace final

| Menace | Contrôle préventif | Détection ou reprise |
|---|---|---|
| Accès Internet anonyme | IDs opaques, secret en header, Host/Origin/CORS bornés, rate limit | réponses 404 uniformes, corrélation expurgée |
| Upload/YAML/ZIP hostile | limites, hashes, parsing strict, no-follow, quotas et validation hors file | codes publics stables, quarantaine et tests de corpus |
| Rejeu ou course | idempotence transactionnelle et `Expected-Revision` | conflit 409 et snapshot REST autoritatif |
| Provider compromis | endpoint opérateur, délais, circuit, IPC borné, aucune réponse brute publique | télémétrie par tentative expurgée et relance contrôlée |
| Fuite de secret ou contenu | secrets montés par fichier, logs et schémas publics minimaux | scans de secrets, tests de non-fuite et rotation |
| Compromission du conteneur | UID non-root, rootfs RO, capabilities supprimées, volumes étroits | smoke d'isolation, digest et scan d'image |
| Corruption ou perte locale | SQLite/WAL colocalisés, écritures atomiques, HMAC de sauvegarde | integrity check, audit opérateur et restauration neuve |
| Épuisement de ressources | limites file, taille, temps, mémoire/PID et seuil disque | ready dégradé, drain et test de charge |
| Chaîne de build altérée | lockfiles, images/actions par digest ou commit, CI sans push | SBOM, scans, checksums et provenance archivés |

## Risques résiduels et limites connues

| Élément | Décision actuelle | Condition de fermeture |
|---|---|---|
| Isolation des jobs dans un process pool partagé | Accepté pour V1 : chemins gérés, no-follow, hashes, IPC borné et conteneur isolé | réévaluer avant exécution de formats ou plugins arbitraires |
| Contrôleurs mémoire absents sur l'hôte ARM local | La CI Ubuntu et l'hôte Linux de livraison font foi | exécuter le test d'épuisement cgroup et archiver le résultat |
| Docker Desktop Windows non exécuté dans la preuve Linux locale | Ne bloque pas le développement Linux ; bloque une affirmation de support Windows pour une release | jouer `scripts/smoke_web_compose.ps1` sur la machine cible |
| Tests Axe automatisés | Ils ne prouvent pas une conformité WCAG AA complète | revue clavier/lecteur d'écran et audit humain avant revendication de conformité |
| Providers externes non appelés par la validation reproductible | Choix volontaire pour éviter coût, secret et instabilité | smoke provider séparé, explicitement autorisé, si requis par l'exploitation |

## Exceptions de sécurité

Aucune exception critique ou élevée n'est acceptée dans le dépôt. Pour une
release, produire et signer une liste, même vide, contenant : identifiant,
gravité, propriétaire, justification, contrôles compensatoires et échéance. Une
signature ou une attestation de release ne peut pas être fabriquée localement
sans l'identité et la clé du responsable ; son absence bloque la décision de
livrer, pas les tests reproductibles.

## Preuves à joindre à une release

- révision/tag, digest et labels OCI de l'image ;
- configuration Compose rendue et expurgée ;
- rapports Pytest, frontend, Playwright et charge ;
- SBOM Python, npm et image avec SHA-256 ;
- résultats SAST, dépendances, secrets, configuration et image ;
- résultat des smokes Linux, cgroup et Docker Desktop requis ;
- manifestes de sauvegarde/restauration, modèle de menace et exceptions signées ;
- provenance/attestation et décision finale signée.

Le dépôt fournit les commandes et contrôles techniques. La signature, le test
sur l'hôte de production cible et la décision de livraison restent des actes
opérateur non substituables par le développement local.

## État de clôture local au 21 août 2026

Tous les critères localement exécutables sont verts sur `00d2be5` : 845 tests
Python, 53 tests Vitest, 60 scénarios navigateur statiques, le scénario
Chromium/Compose réel, 74 tests adversariaux, les audits de dépendances, Bandit,
les scans source/image, les SBOM, les checksums et les deux smokes Compose.

La clôture d'une release distribuée reste suspendue aux trois preuves externes
identifiées : attestation OIDC effectivement émise par GitHub, contrôleur
mémoire cgroup actif sur l'hôte Linux cible et smoke Docker Desktop Windows. Le
workflow et la checklist sont prêts à les produire ou à bloquer la livraison ;
aucune réussite n'est simulée dans ce rapport.
