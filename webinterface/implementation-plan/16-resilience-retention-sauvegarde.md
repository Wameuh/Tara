# Etape 16 - Resilience, retention et sauvegarde

## Objectif

Finaliser les comportements de redemarrage, expiration, relance, arret controle, sauvegarde et restauration pour une exploitation durable.

## Dependances

Etapes 12 a 15.

## Fichiers a creer ou modifier

- `src/tara_web/lifecycle/startup.py`, `shutdown.py`, `drain.py`.
- `src/tara_web/storage/cleanup.py`, `reconciliation.py`, `backup.py`.
- `src/tara_web/services/relaunch.py`.
- commandes CLI d'exploitation dans `src/tara_web/cli.py`.
- tests d'horloge, crash, sauvegarde et restauration.
- `docs/webinterface/operations/backup-restore.md`.

## Demarrage

Executer dans cet ordre:

1. validation de configuration et langues;
2. controle de version SQLite;
3. ouverture WAL et verification d'integrite rapide;
4. reconciliation des chunks, promotions et artefacts;
5. passage des jobs actifs orphelins en echec d'interruption;
6. restauration FIFO des jobs en attente;
7. restauration des circuits provider;
8. demarrage scheduler, nettoyage et serveur pret.

## Retention

- Intermediaires: `uploaded_at + 24h` au maximum.
- YAML final: `ready_at + 7 jours`.
- Session incomplete: valeur configurable, 24h par defaut.
- Nettoyage horaire par `expires_at`, avec etats explicites `resultat expire` ou `job supprime` conserves jusqu'a expiration des metadonnees.
- Une relance cree un nouveau job, une nouvelle tentative logique et remet son compteur de retention a zero.
- Les fichiers annules restent jusqu'a la retention normale; l'annulation explicite d'une session incomplete les supprime immediatement.

`Modifier et relancer` copie les references encore valides dans une nouvelle session editable. `Relancer a l'identique` apres timeout est autorise une seule fois et cree toujours un nouveau job.

## Arret controle

Passer en drain, refuser les nouveaux lancements, laisser les operations actives pendant le delai configurable, demander l'annulation cooperative, reconciler, puis sauvegarder. Les lectures de resultats peuvent rester disponibles tant que possible.

Sous Docker, `SIGTERM` declenche exactement cette sequence. Le serveur devient non pret des le drain, et `stop_grace_period` doit depasser le delai applicatif plus la reconciliation/sauvegarde. Un `SIGKILL` reste traite comme crash au demarrage suivant.

## Sauvegarde

Lors de l'arret controle seulement:

1. creer un snapshot SQLite via l'API de backup;
2. verifier son integrite;
3. copier les YAML finaux `ready` apres verification SHA-256;
4. ecrire un manifeste avec versions et expirations;
5. publier atomiquement la sauvegarde dans une destination distincte et chiffree au repos.

Ne jamais sauvegarder audio ou intermediaires. Une sauvegarde ne survit pas a l'expiration de la source. La restauration est manuelle, hors ligne, verifie manifeste, schema, empreintes et compatibilite avant activation.

## Securite

- La destination de sauvegarde est hors racine publique, chiffree au repos, avec permissions minimales et credentials distincts de l'application courante.
- Le manifeste est authentifie ou signe avec une cle d'exploitation afin de detecter substitution et rollback non autorise; la cle n'est pas stockee dans la sauvegarde.
- Une restauration s'effectue hors ligne dans une nouvelle racine, refuse symlinks/chemins absolus et ne remplace l'etat actif qu'apres toutes les validations.
- Nettoyer aussi les sauvegardes selon l'expiration source et verifier qu'aucun YAML final expire ne reapparait apres restauration.
- Les commandes drain, backup, restore et migration sont reservees a l'exploitation locale/privee et produisent un audit expurge.
- Tester la recuperation face a une sauvegarde partielle, ancienne, modifiee, zippee de maniere hostile ou issue d'une version plus recente.

## Validation

- Tests avec horloge injectee aux bornes 24h et 7 jours.
- Crash et redemarrage sur chaque etat non terminal.
- Drain avec jobs rapides, lents et non interruptibles.
- Sauvegarde coherent SQLite/YAML, echec de destination et restauration sur instance vide.
- Refus d'une sauvegarde corrompue, plus recente ou contenant un resultat expire.
- Une sauvegarde echouee ne modifie pas le succes des jobs mais rend l'operation d'arret en echec observable.
- Une sauvegarde alteree ou rejouee est refusee avant toute modification de l'instance active.
- La sauvegarde et la restauration sont testees avec les volumes Compose; la restauration exige que `tara-web` soit arrete et ne monte jamais simultanement la base active.

## Definition de fin

Les procedures automatiques convergent apres incident et la sauvegarde/restauration a ete executee avec succes sur un jeu representatif.
