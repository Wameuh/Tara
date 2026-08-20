# Sauvegarde et restauration

Tara crée une sauvegarde uniquement pendant un arrêt contrôlé et seulement si
`webinterface.backup.enabled` vaut `true`. Dès le signal `SIGTERM`, l'instance
devient non prête, refuse les nouveaux lancements, laisse les workers terminer
pendant `shutdown_grace_seconds`, puis demande l'annulation coopérative des
workers restants. La réconciliation précède le snapshot.

## Contenu et garanties

Une génération `backup-<UTC>-<identifiant>` contient :

- un snapshot SQLite produit avec l'API de sauvegarde SQLite et vérifié par
  `PRAGMA integrity_check` ;
- uniquement les YAML finaux encore valides, vérifiés avec SHA-256 ;
- un manifeste canonique et son HMAC SHA-256.

Les audios, chunks et intermédiaires ne sont jamais copiés. Une génération
devient inutilisable dès qu'un de ses résultats arrive à expiration et le
nettoyage de l'arrêt suivant la supprime. La clé HMAC n'est jamais écrite dans
la génération.

Le chemin `backups_root` doit être un arbre distinct de `storage.root`, privé
(`0700`) et placé sur un stockage chiffré au repos. La clé d'exploitation doit
contenir au moins 32 octets :

```bash
export TARA_WEB_BACKUP_SIGNING_KEY='une-cle-secrete-distincte-de-32-octets-minimum'
```

Une sauvegarde manuelle hors serveur utilise la même primitive :

```bash
tara-web-operator --config /config/webinterface.yaml backup
```

## Restauration hors ligne

La restauration refuse un manifeste altéré, un schéma ou format plus récent,
un chemin absolu ou traversant, un lien symbolique, un fichier au mauvais hash
et tout résultat expiré. Toutes les vérifications terminent avant l'activation
atomique de la nouvelle racine.

1. Arrêter `tara-web` et vérifier qu'aucun processus ne monte la base cible en
   écriture.
2. Préparer une configuration opérateur dont `storage.root` désigne une racine
   **inexistante** et dont `sqlite_path` se trouve sous cette racine.
3. Monter la génération en lecture seule et la nouvelle racine sur un volume
   différent de la base active.
4. Exécuter :

```bash
tara-web-operator \
  --config /config/webinterface-restore.yaml \
  restore /backups/backup-20260820T120000Z-0123456789abcdef0123456789abcdef \
  --not-before 2026-08-20T00:00:00+00:00
```

Le checkpoint `--not-before` doit provenir du journal d'exploitation externe. Il
empêche le rejeu d'une génération authentique mais antérieure à la dernière
génération approuvée.

5. Contrôler l'audit `operator_action_audit`, puis démarrer une instance Tara
   pointant uniquement vers la nouvelle racine. Ne jamais monter simultanément
   l'ancienne et la nouvelle base sur `tara-web`.

Une destination partielle n'est jamais publiée. En revanche, une erreur de
sauvegarde rend l'arrêt contrôlé observable en échec ; elle ne modifie ni le
statut ni le résultat d'un job déjà terminé.
