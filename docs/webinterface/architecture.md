# Architecture des contrats Web Tara

## Frontieres et dependances

`tara_web` est le proprietaire unique du cycle de vie web : API, snapshots,
revisions, SQLite, stockage gere et diffusion SSE. Il depend de `tara` pour le
protocole `tara.web_contracts`, jamais l'inverse. Un worker Tara ne recoit qu'un
`RunnerRequest` immuable, les chemins relatifs deja autorises pour son job, un
sink d'evenements et un jeton d'annulation. Il ne lit ni SQLite, ni le secret
proprietaire, ni les objets FastAPI.

Les messages worker → processus principal sont des primitives JSON bornees. Le
processus principal les valide, applique la transition et la revision, puis
construit les schemas publics. Le frontend ne deduit ni etat ni permission : il
affiche le snapshot et `allowed_actions`, qui sont revalidees au moment de la
commande.

## Flux et recuperation

1. Une session publique commence dans un niveau provisoire borne et de courte
   duree. La premiere declaration de fichier reserve atomiquement une place
   active, puis la session recoit des fichiers reprenables.
   Pour l'audio, la selection cree la session et demarre les transferts sans
   bloquer la saisie du contexte et des resumes anterieurs. Un conflit de chunk
   `409 upload_chunk_conflict` provoque une relecture de l'offset serveur et une
   reprise automatique bornee.
2. Chaque fichier est finalise et valide hors de la file Tara.
3. Une session prete est revendiquee atomiquement et cree un job FIFO.
4. Le runner declare uniquement evenements et artefacts; le processus principal
   promeut le resultat et publie un snapshot.
5. Une annulation est cooperative; un retour tardif est ignore. Des que le job
   devient terminal, uploads, entrees et fichiers de travail sont supprimes
   recursivement. Un succes conserve uniquement son resultat final valide; les
   autres issues suppriment aussi tout resultat partiel. Une relance editable
   demande donc de nouvelles entrees.
6. Au redemarrage, les jobs actifs sans worker deviennent `failed` avec
   `server_interrupted`; les jobs en attente conservent leur ordre. Les uploads
   et artefacts sont reconcilies depuis leur etat persiste.
7. Le resultat public contient la projection structuree et, lorsqu'il est
   disponible, le texte exact de `session_summary.md`. Le frontend telecharge
   ce texte sans transformation et le rend a l'ecran avec un parseur Markdown
   charge a la demande. Le HTML brut, les images et les URL dangereuses ne sont
   pas rendus.

## Classification et acces minimal

| Classe | Exemples | Regle |
|---|---|---|
| public | statuts, codes, progression, expiration, identifiant de correlation d'une reponse en erreur | schemas API et SSE uniquement |
| interne | chemins relatifs, detail de retry, diagnostic technique | processus principal et logs expurges |
| sensible | noms originaux, contenu utilisateur, artefacts | stockage gere et routes protegees |
| secret | lien proprietaire complet, preuve de recuperation de creation, cles HMAC, credentials provider | capacites au porteur, jamais dans IPC, schemas publics ou logs |

La possession du lien proprietaire vaut autorisation complete sur sa ressource ;
aucune identite n'est verifiee au-dela de cette capacite. Le processus HTTP peut
lire sa configuration et secrets, ecrire SQLite et les
racines gerees, et soumettre des workers. Le worker peut lire les seules entrees
du job et les credentials strictement requis; il ne peut pas muter la base ni
publier un artefact. Le frontend ne recoit que les donnees publiques et le
secret transmis dans le fragment local de l'URL.

## Menaces et controles

| Menace | Controle contractuel |
|---|---|
| Attaquant Internet | identifiants opaques, actions et types inconnus refuses, payloads bornes |
| Detenteur d'un lien | fragment absent de la requete HTTP mais lisible par le navigateur, avertissement de capacite au porteur, rotation atomique et revocation de l'ancien secret |
| Fichier hostile | chemins relatifs geres, validation positive, tailles et evenements bornes |
| Provider compromis | erreurs converties en codes stables; reponses brutes et credentials restent internes |
| Operateur mal configure | dependances a sens unique, privileges minimaux et listes positives de contrats |

## Matrice de tracabilite

| Decision structurelle de DESIGN | Etape proprietaire |
|---|---:|
| Contrats runner, IPC, etats, revisions et vocabulaire stable | 00 |
| Socle FastAPI/React, configuration et cycle de vie | 01 |
| SQLite, idempotence, secrets derives et migrations | 02 |
| Stockage, integrite et retention des artefacts | 03 |
| Upload reprenable et finalisation | 04 |
| FIFO, process pool, faux runner, annulation et reprise | 05 |
| REST, SSE, secret de lien et securite HTTP | 06 |
| Frontend fonctionnel | 07 |
| Specifications visuelles et concepts | 07A |
| Logo et animation | 07B |
| Validation verticale du MVP | 08 |
| Schemas YAML versionnes | 09 |
| Adaptateur du runner Tara reel | 10 |
| Providers, Modal et telemetrie | 11 |
| Integration audio reelle | 12 |
| Entree merged transcription | 13 |
| Entree ZIP | 14 |
| Estimation, cout et budget | 15 |
| Resilience, retention et sauvegarde | 16 |
| Deploiement Docker | 17 |
| Durcissement et livraison V1 | 18 |

Les changements incompatibles de ces contrats exigent une nouvelle version
majeure d'API ou un adaptateur explicite.
