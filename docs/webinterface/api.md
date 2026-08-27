# Guide de l'API HTTP V1

L'API stable utilise le préfixe `/api/v1`. Le contrat machine canonique est
`webinterface/frontend/src/api/openapi.json`; il est régénéré et comparé en CI.
La documentation interactive est désactivée en production.

## Autorisation et liens partageables

La création d'une session renvoie un `session_id` opaque et un `secret`. Une
reprise de la réponse de création peut renvoyer le même secret uniquement avec
la preuve de récupération fournie lors de la requête initiale. Le frontend
place ensuite le secret dans le fragment `#secret=...` du lien : un fragment
n'est pas envoyé au serveur par HTTP. Pour chaque lecture ou mutation protégée,
le client transmet :

```http
X-Tara-Job-Secret: <secret>
```

Le lien complet est une capacité propriétaire au porteur : sa possession suffit
pour lire et modifier la ressource, sans contrôle d'identité supplémentaire.
Le secret ne doit apparaître ni dans le chemin, ni dans la query string, ni
dans les logs. Le choix d'un fragment conserve les liens rafraîchissables et
partageables, mais le lien doit être protégé comme un mot de passe contre
l'historique, les extensions, le presse-papiers, les captures et le partage
d'écran. Une ressource inconnue et un secret incorrect produisent la même
réponse `404`. La rotation via `POST /api/v1/jobs/{job_id}/secret` révoque
l'ancien secret atomiquement.

## Concurrence, révision et idempotence

Toute création ou mutation rejouable porte une clé opaque propre à l'opération :

```http
Idempotency-Key: <uuid-ou-valeur-opaque>
Expected-Revision: <revision-lue-dans-le-snapshot>
```

Une même clé avec le même payload rejoue le résultat sans doubler l'effet. Une
clé réutilisée avec un payload différent, une révision obsolète ou une action
qui n'est plus permise produit `409`. Une précondition absente produit `428`
sur les commandes qui exigent une révision. Le client doit relire le snapshot
REST avant de décider d'une nouvelle action.

La création publique exige en plus une preuve distincte de 32 octets aléatoires,
encodée en base64url canonique sans remplissage :

```http
X-Tara-Creation-Recovery: <43-caractères-base64url>
```

`Idempotency-Key` seule ne permet jamais de récupérer le secret propriétaire.
Après une réponse perdue, le client rejoue une fois la création avec exactement
les deux mêmes valeurs. La preuve de récupération est un credential au porteur :
elle n'est ni journalisée, ni placée dans une URL, ni conservée après la reprise.

## Parcours principal

1. `POST /api/v1/uploads/sessions?input_type=audio|merged_transcription|zip`
   crée une session provisoire, bornée globalement et par identité réseau. Elle
   expire après 30 minutes sans première déclaration de fichier.
2. `POST /api/v1/uploads/sessions/{session_id}/files` déclare un fichier avec
   taille et SHA-256. Cette première déclaration réserve atomiquement une place
   active et porte l'expiration à 24 heures ; si la capacité active est pleine,
   la déclaration est refusée sans créer le fichier.
3. `PATCH .../files/{file_id}/chunks` envoie un chunk séquentiel avec
   `Upload-Offset` et `Upload-Checksum`. `GET .../offset` permet la reprise. En
   cas de `409 upload_chunk_conflict`, le client relit l'offset confirmé puis
   réessaie de façon bornée ; l'état local du navigateur n'est jamais la source
   de vérité.
4. `POST .../files/{file_id}/finalize` déclenche la validation. Le snapshot de
   session expose son avancement et les erreurs publiques.
5. `PATCH /api/v1/sessions/{session_id}/inputs` fixe langue, contexte et
   résumés antérieurs, puis `POST /api/v1/sessions/{session_id}/jobs` lance le
   job lorsque la session est prête.
6. `GET /api/v1/jobs/{job_id}` est la source de vérité. Le client peut annuler,
   créer une relance éditable ou régénérer le secret si l'action figure dans
   `allowed_actions`. À l'état terminal, les sources et intermédiaires sont
   supprimés : la relance identique est désactivée et une relance éditable crée
   toujours une session vide exigeant de nouvelles entrées.
7. `GET /api/v1/jobs/{job_id}/result` expose uniquement la projection publique
   versionnée, jamais le YAML brut ni un chemin interne. Lorsque
   `summary_markdown` est présent, il contient exactement le document
   `session_summary.md` publié : le téléchargement le conserve tel quel et le
   rendu HTML côté navigateur reste une présentation non canonique.
8. Après succès, échec, timeout ou annulation, le backend supprime récursivement
   les uploads, entrées et fichiers de travail. Seul le résultat final validé
   d'un job réussi est conservé jusqu'à son échéance de sept jours. Un cycle au
   démarrage puis périodique reprend tout nettoyage interrompu.

Les endpoints de configuration et de santé sont
`GET /api/v1/config/public`, `GET /api/v1/live` et `GET /api/v1/ready`.

## Webhook Ko-fi

`POST /api/v1/funding/kofi/webhook` accepte le format Ko-fi
`application/x-www-form-urlencoded` : le champ `data` contient le paiement en
JSON. Le token est comparé en temps constant au secret monté dans le conteneur.
Une livraison valide, y compris un doublon de `message_id`, reçoit `200` afin
d'arrêter les nouvelles tentatives Ko-fi.

Chaque livraison produit aussi une ligne structurée dans les journaux de
`tara-web` : `kofi_webhook_received` précise le type, la devise, le marqueur de
test, si le montant a été compté et si le `message_id` était nouveau. Un rejet
produit `kofi_webhook_rejected` avec une raison générique, sans jamais écrire le
token ni les données personnelles. Les tests Ko-fi sont conservés avec leur
montant et le marqueur `is_test_transaction`, mais exclus du cumul public.

La jauge additionne en EUR les événements `Tip`, `Subscription` et l'ancien
libellé `Donation` encore utilisé par certains tests Ko-fi. `Commission` et
`Shop Order` sont acceptés mais ne contribuent pas à la jauge. Tara ne conserve
et n'affiche jamais `from_name`, `email`, `message`, `tier_name`, `shop_items`
ou les détails d'expédition. Le message reste donc privé indépendamment de
`is_public` ; seuls des totaux mensuels agrégés sont publics.

## Statistiques de vues

`POST /api/v1/metrics/page-view?page=...` est appelé par le frontend à chaque
vue de haut niveau. Les seules valeurs acceptées sont `new_job`, `help`,
`upload_session` et `job`. La base conserve uniquement un compteur agrégé par
jour UTC et par vue : aucune IP, aucun cookie, aucun identifiant de visiteur et
aucun identifiant de job ou de session n'est enregistré.

## SSE et repli REST

`GET /api/v1/jobs/{job_id}/events` répond en `text/event-stream`. Chaque
événement est une notification légère `snapshot_updated` avec une révision ;
il ne remplace jamais le snapshot REST. Les heartbeats maintiennent la
connexion, `Cache-Control: no-store` interdit le cache et le proxy désactive le
buffering.

Après coupure, limitation `429` ou événement inconnu, le client attend avec
jitter puis repasse au polling de `GET /jobs/{job_id}`. Il ne déduit jamais un
statut terminal à partir de l'absence d'événement.

## Statuts et erreurs

Les sessions progressent notamment de `created` vers `uploading`,
`validating`, `ready` puis `promoted`. Les jobs utilisent `queued`, `running`,
`completed`, `failed`, `cancelled`, `timed_out`, `expired` et `deleted`. Seuls
le snapshot et `allowed_actions` autorisent une commande.

Les erreurs utilisent `application/problem+json` avec un statut HTTP, un
`code` stable et un `correlation_id` opaque. Le frontend présente ce dernier
comme `Code support` ; l'opérateur peut rechercher la même valeur dans les logs
expurgés. Aucun message technique, prompt, chemin ou contenu provider n'est
public. Les codes métier et paramètres autorisés sont listés dans
[Vocabulaire public stable](error-codes.md). Les réponses `429` et `503`
doivent respecter un délai borné ; une mutation n'est rejouée qu'avec sa même
clé d'idempotence.

## Versionnement

- Une rupture HTTP exige un nouveau préfixe majeur, par exemple `/api/v2`.
- Les ajouts compatibles restent dans V1 ; les clients ignorent les champs et
  événements inconnus puis relisent le snapshot.
- Les documents YAML portent leur propre `schema_name` et `schema_version`.
- Le frontend généré et l'OpenAPI versionné doivent être modifiés dans le même
  commit que tout changement de contrat.
