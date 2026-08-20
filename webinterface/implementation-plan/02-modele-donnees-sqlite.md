# Etape 02 - Modele de donnees SQLite

## Objectif

Implementer la persistence transactionnelle des sessions, fichiers, jobs, artefacts, idempotence, circuits et telemetries. SQLite devient la source de verite des etats, jamais le contenu du navigateur ou les workers.

## Dependances

Etapes 00 et 01.

## Fichiers a creer ou modifier

- `src/tara_web/db/connection.py`: connexions, WAL, foreign keys, busy timeout.
- `src/tara_web/db/migrations.py`: registre et execution des migrations.
- `src/tara_web/db/migrations/0001_initial.sql` et migrations suivantes.
- `src/tara_web/db/repositories/`: un repository par agregat.
- `src/tara_web/domain/models.py`: modeles internes sans dependance HTTP.
- `src/tara_web/services/idempotency.py`.
- `tests/web/db/`: creation, migration, concurrence et rollback.
- tests SQLite sur le volume Docker reel utilise par le MVP.

## Schema initial attendu

Tables operationnelles minimales:

- `upload_sessions`, `upload_files`, `upload_chunks` ou etat d'offset equivalent;
- `jobs`, `job_attempts`, `job_artifacts`;
- `idempotency_keys`, `provider_circuits`;
- `job_metrics`, `job_failure_metrics`, `pre_job_error_metrics`;
- table de version globale du schema.

Les secrets sont stockes sous forme de derivee lente et salee ou HMAC serveur, jamais en clair. Les identifiants sont opaques et independants des cles SQLite internes. Les timestamps sont stockes en UTC et les montants en entiers dans une unite minimale documentee, puis formates en euros a cinq decimales.

## Transactions critiques

### Reservation et creation de session

Compter les reservations actives et jobs en attente dans une transaction immediate courte. Inserer la reservation uniquement si la capacite globale et le seuil disque le permettent.

### Promotion session vers job

Verifier session, fichiers et revision attendue; creer le job, revendiquer la session et enregistrer la tentative initiale dans une seule transaction. Une contrainte unique interdit deux jobs pour la meme intention de lancement.

### Mutation avec revision

Utiliser une mise a jour conditionnelle `WHERE id = ? AND revision = ?`, incrementer `revision`, puis retourner le snapshot. Zero ligne modifiee produit un conflit et une relecture canonique.

### Idempotence

Canoniser la requete, calculer son empreinte, reserver la cle, executer l'operation puis attacher le resultat. Meme cle et meme empreinte renvoient le resultat existant; une empreinte differente produit un conflit.

## Migrations

- Chaque migration est ordonnee, transactionnelle si SQLite le permet, et testee depuis toute version supportee.
- Le demarrage refuse une base plus recente.
- En production, la migration est declenchee explicitement pendant une maintenance apres sauvegarde.
- Une base neuve et une base migree doivent aboutir au meme schema logique.

SQLite, WAL et SHM doivent rester ensemble dans un volume Docker local dedie. Ne pas placer la base sur la couche writable de l'image, un `tmpfs` ou un volume reseau. Verifier les permissions UID/GID avant ouverture et documenter le changement controle d'identite du conteneur.

## Securite

- Utiliser exclusivement des requetes parametrees; aucun nom de table, colonne, filtre ou ordre provenant d'une requete HTTP ne doit etre concatene au SQL.
- Appliquer le moindre privilege au fichier SQLite, au WAL et aux sauvegardes; refuser une base qui est un lien symbolique ou sort de la racine configuree.
- Proteger les secrets par HMAC avec une cle serveur separee et rotation versionnee; comparer les empreintes en temps constant.
- Borner la taille des champs, la retention des cles d'idempotence et le nombre de lignes creees par une session pour eviter le gonflement de base.
- Masquer secrets, noms de fichiers et contenu utilisateur dans les exceptions de repository et traces SQL.
- Verifier l'integrite SQLite et la version avant migration; ne jamais tenter de reparer automatiquement une base non fiable en production.

## Validation

- WAL, foreign keys et busy timeout sont effectivement actifs sur chaque connexion.
- Les workers n'importent pas les repositories et n'ecrivent jamais dans SQLite.
- Deux creations concurrentes avec la meme cle sont idempotentes.
- Deux commandes avec la meme revision ne peuvent pas toutes deux gagner.
- Les contraintes de capacite ne sont jamais depassees sous concurrence simulee.
- Toutes les migrations montantes supportees et les rollbacks transactionnels sont testes.
- Les tests couvrent injection SQL, contention abusive, base symlinkee, secret faux et fichier SQLite aux permissions incorrectes.
- Un redemarrage et une recreation du conteneur conservent la base/WAL sans corruption ni changement de proprietaire inattendu.

## Definition de fin

Le schema initial est reproductible, versionne, teste en concurrence et documente par un diagramme relationnel maintenu avec les migrations.
