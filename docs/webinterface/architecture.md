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

1. Une session reserve de la capacite, puis recoit des fichiers reprenables.
2. Chaque fichier est finalise et valide hors de la file Tara.
3. Une session prete est revendiquee atomiquement et cree un job FIFO.
4. Le runner declare uniquement evenements et artefacts; le processus principal
   promeut le resultat et publie un snapshot.
5. Une annulation est cooperative; un retour tardif est ignore. Les entrees sont
   conservees jusqu'a la retention normale pour permettre une relance.
6. Au redemarrage, les jobs actifs sans worker deviennent `failed` avec
   `server_interrupted`; les jobs en attente conservent leur ordre. Les uploads
   et artefacts sont reconcilies depuis leur etat persiste.

## Classification et acces minimal

| Classe | Exemples | Regle |
|---|---|---|
| public | statuts, codes, progression, expiration | schemas API et SSE uniquement |
| interne | chemins relatifs, detail de retry, correlation | processus principal et logs expurges |
| sensible | noms originaux, contenu utilisateur, artefacts | stockage gere et routes protegees |
| secret | secret de lien, cles HMAC, credentials provider | jamais dans IPC, schemas publics ou logs |

Le processus HTTP peut lire sa configuration et secrets, ecrire SQLite et les
racines gerees, et soumettre des workers. Le worker peut lire les seules entrees
du job et les credentials strictement requis; il ne peut pas muter la base ni
publier un artefact. Le frontend ne recoit que les donnees publiques et le
secret transmis dans le fragment local de l'URL.

## Menaces et controles

| Menace | Controle contractuel |
|---|---|
| Attaquant Internet | identifiants opaques, actions et types inconnus refuses, payloads bornes |
| Detenteur d'un lien | secret hors URL serveur, snapshots sans fuite interne, regeneration future du secret |
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
