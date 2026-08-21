# Checklist de livraison V1

Créer un dossier d'artefacts vide pour chaque version et cocher chaque point à
partir d'un checkout propre. Une case non cochée bloque la livraison, sauf
exception de sécurité explicitement acceptée, datée et signée par le
responsable désigné.

## Version et reproductibilité

- [ ] Révision Git, tag, date UTC, opérateur et machine de référence consignés.
- [ ] Worktree propre ; lockfiles Python/npm et actions CI inchangés pendant la
  validation.
- [ ] Image reconstruite avec les labels OCI source, révision et version ; son
  digest et sa provenance SLSA signée sont archivés et vérifiables.
- [ ] Configuration Compose rendue et expurgée archivée.

## Qualité et contrats

- [ ] Ruff et suite Pytest complète réussis, migrations neuves et mises à niveau
  incluses.
- [ ] ESLint, Vitest, TypeScript, build, budget bundle, OpenAPI et manifestes
  i18n réussis.
- [ ] Chromium, Firefox, WebKit et Chromium mobile réussis avec Axe ; la limite
  de la couverture automatisée WCAG AA est rappelée.
- [ ] MP3/OGG multi-pistes, merged YAML et ZIP passent les parcours réels ou les
  doubles provider déterministes approuvés.
- [ ] Charge 5 actifs + 25 en attente respecte les seuils p95, promotion,
  intégrité SQLite et disponibilité.

## Sécurité et chaîne de livraison

- [ ] `pip-audit` et `npm audit` ne signalent aucune vulnérabilité exploitable
  HIGH/CRITICAL.
- [ ] Bandit SAST au seuil HIGH, secret scan, scan de configuration et scan
  d'image sont réussis.
- [ ] SBOM Python, npm et image, rapports de scan et versions des outils sont
  archivés avec leurs SHA-256.
- [ ] Le modèle de menace est relu ; toute exception possède propriétaire,
  justification, compensation, échéance et signature.
- [ ] Aucun debug, faux runner, docs publiques, credential de test, origine ou
  hôte joker n'est actif dans la configuration rendue.
- [ ] Le garde adversarial dynamique couvre API, upload, média polyglotte, YAML,
  ZIP et SSE sans donnée réelle ; son rapport JUnit est archivé.

## Déploiement et reprise

- [ ] Smoke Compose Linux : TLS, live/ready, UID non-root, rootfs lecture seule,
  capabilities, montages et ports.
- [ ] Limites mémoire/PID testées sur un hôte avec contrôleurs cgroup actifs.
- [ ] Upload reprenable, SSE, résultat, `SIGTERM`, drain et redémarrage réussis
  derrière le proxy.
- [ ] Sauvegarde authentifiée, migration explicite et restauration dans un
  volume neuf réussies avec `PRAGMA integrity_check`.
- [ ] Parcours Docker Desktop Windows exécuté ou écart bloquant consigné.
- [ ] Stockage local chiffré, capacité disque, rétention, budget, circuits,
  rotation des secrets et contacts d'incident vérifiés.

## Artefacts et décision

- [ ] Rapports de tests, configuration Compose, digest, SBOM, scans,
  attestations/provenance et contrôles d'isolation sont archivés.
- [ ] Les risques résiduels et limites connues de `v1-validation.md` ont un
  propriétaire et une décision explicite.
- [ ] La liste d'exceptions acceptées, même vide, est signée et archivée.
- [ ] Le responsable de livraison date et signe la décision **livrer/refuser**.

Commandes de référence : [Validation V1](mvp-validation.md). Procédures
d'exploitation : [Runbook](runbook.md).
