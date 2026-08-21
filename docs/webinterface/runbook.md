# Runbook d'exploitation V1

Ce document est le point d'entrée opérateur. Docker Compose est le seul mode
d'exploitation supporté pour une instance partagée ou exposée. Les commandes
détaillées et les permissions attendues sont décrites dans le
[guide de déploiement](docker-deployment.md) et la procédure cryptographique
dans [Sauvegarde et restauration](operations/backup-restore.md).

## Installation et configuration

1. Utiliser un hôte Linux avec Docker Engine, Compose v2, un pare-feu et des
   volumes locaux chiffrés au repos. Ne jamais placer SQLite sur NFS ou SMB.
2. Copier `compose.override.yaml.example` vers un fichier non versionné et
   préparer le certificat TLS, sa clé, la clé de signature des sauvegardes et
   les credentials provider sous forme de fichiers privés.
3. Définir les chemins `TARA_WEB_TLS_CERTIFICATE_FILE`,
   `TARA_WEB_TLS_PRIVATE_KEY_FILE`, `TARA_WEB_BACKUP_KEY_FILE`,
   `MODAL_TOKEN_ID_FILE` et `MODAL_TOKEN_SECRET_FILE`.
4. Vérifier `public_url`, les hôtes et origines autorisés, les sous-réseaux de
   proxy de confiance, le budget, les capacités, les délais et les limites de
   ressources. Aucun joker, faux runner, debug ou documentation interactive
   n'est admis en production.
5. Exécuter `docker compose -f compose.yaml -f compose.override.yaml config
   --quiet`, puis construire et scanner l'image avant le démarrage.

## Démarrage, santé et arrêt contrôlé

```bash
docker compose -f compose.yaml -f compose.override.yaml up -d
docker compose -f compose.yaml -f compose.override.yaml ps
curl --fail --cacert secrets/tls.crt https://localhost:8443/api/v1/live
curl --fail --cacert secrets/tls.crt https://localhost:8443/api/v1/ready
```

`live` prouve que le processus répond. `ready` vérifie que l'instance accepte
le trafic et n'est ni en drain ni bloquée par le stockage, SQLite ou un circuit
d'exploitation. Seul `tara-proxy` publie un port.

Pour drainer, envoyer `SIGTERM` avec Compose :

```bash
docker compose -f compose.yaml -f compose.override.yaml stop tara-web
```

L'instance devient immédiatement non prête, refuse les nouveaux lancements,
laisse les jobs terminer pendant le délai configuré, annule coopérativement les
autres, réconcilie puis sauvegarde. Ne pas réduire `stop_grace_period` sous le
délai applicatif.

## Sauvegarde, migration et restauration

Toujours drainer avant une opération SQLite explicite :

```bash
docker compose -f compose.yaml -f compose.override.yaml stop tara-web
docker compose -f compose.yaml -f compose.override.yaml \
  --profile operations run --rm tara-web-backup
docker compose -f compose.yaml -f compose.override.yaml \
  run --rm tara-web-migrate
```

Une restauration s'effectue hors ligne dans un volume neuf. Définir
`TARA_RESTORE_GENERATION`, lancer le profil `restore`, vérifier le manifeste,
le HMAC, `PRAGMA integrity_check` et l'audit opérateur, puis seulement basculer
vers la nouvelle racine. Ne jamais monter simultanément l'ancienne et la
nouvelle base dans l'application.

## Rotation et révocation

- Certificat TLS : installer la nouvelle paire, valider la chaîne, recréer le
  proxy puis révoquer l'ancien certificat.
- Credentials provider : créer un credential de remplacement, le monter,
  recréer `tara-web`, vérifier un appel borné, puis révoquer l'ancien.
- Clé HMAC d'upload : sa rotation invalide les secrets de liens existants ;
  planifier une fenêtre et prévenir les utilisateurs avant la bascule.
- Clé de signature des sauvegardes : conserver l'ancienne clé hors ligne aussi
  longtemps qu'une génération correspondante doit rester restaurable. Toute
  nouvelle sauvegarde utilise uniquement la nouvelle clé.
- Secret d'un job : utiliser `POST /api/v1/jobs/{job_id}/secret`; l'ancien
  secret est révoqué atomiquement.

Ne jamais journaliser une valeur secrète. Après rotation, rechercher seulement
son empreinte ou son identifiant public dans les journaux expurgés.

## Surveillance et incidents courants

- **File ou budget saturé** : contrôler les limites 5 actifs/25 en attente, le
  budget journalier et les circuits. Ne pas augmenter la concurrence sans
  recalculer mémoire, PID, FFmpeg et capacité provider.
- **Provider indisponible** : conserver le circuit ouvert, vérifier le statut
  externe et les credentials, puis effectuer une reprise contrôlée. Ne pas
  relancer en boucle et ne jamais exposer la réponse provider brute.
- **Disque sous le seuil** : passer en drain, suspendre les nouveaux uploads,
  exécuter la rétention prévue et étendre le volume. Ne pas supprimer
  manuellement SQLite, son WAL ou un workspace actif.
- **Artefact corrompu** : isoler le job, préserver son identifiant de
  corrélation et les hashes, refuser le résultat, restaurer une génération
  authentifiée ou relancer depuis des entrées encore valides.
- **SQLite non intègre** : arrêter l'instance, conserver une copie en lecture
  seule, restaurer dans un volume neuf et ne pas tenter une réparation en
  production.

## Réponse à incident

1. Confiner : drainer l'instance, fermer l'exposition réseau concernée et
   révoquer les credentials compromis sans détruire les volumes.
2. Préserver : copier les journaux expurgés, digests d'image, SBOM, rapports de
   scan, manifestes de sauvegarde et identifiants de corrélation. Ne pas
   collecter les contenus utilisateur sans nécessité et autorisation.
3. Évaluer : dater l'incident, identifier versions, jobs et secrets affectés,
   puis consigner la décision de notification selon les obligations applicables.
4. Corriger : reconstruire depuis le lockfile, appliquer la rotation nécessaire
   et restaurer uniquement une génération authentifiée.
5. Revalider : exécuter les tests de non-régression, le smoke Compose, les scans
   et les contrôles de santé avant réouverture.

La [checklist de livraison](release-checklist.md) rassemble les preuves à
archiver pour chaque version.
