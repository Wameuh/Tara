# Machines d'etats Web Tara

Les tables executables sont dans `tara_web.domain.state_machines`. Toute paire
etat/commande absente est refusee. Les revisions sont persistantes et doivent
strictement augmenter apres toute mutation visible.

## Sessions

`created → uploading → validating → ready → consumed` est le chemin nominal.
`validating` ou `ready` peut aller vers `waiting_for_capacity`, qui retourne a
`ready` lorsque la reservation est disponible. Toute session non expiree peut
etre annulee selon la table; `expired` est terminal. Une session consommee ne
redevient jamais modifiable.

## Fichiers

`created → uploading → finalizing → verifying → ready`. La verification peut
produire `invalid`; un fichier `ready` ou `invalid` peut etre `replaced` ou
`deleted`. Les etats `replaced` et `deleted` sont terminaux.

## Validations de finalisation

Les controles asynchrones possedent leur propre machine :
`queued -> running -> completed|failed`, avec l'annulation cooperative
`running -> cancel_requested -> stopping -> cancelled|cancel_failed`.
Lors d'un redemarrage controle, une validation `running` revient a `queued` et
est reprise par la file durable. Une annulation deja demandee (`cancel_requested`
ou `stopping`) devient `cancelled`, car le processus de validation est arrete
avant le redemarrage; elle n'est jamais relancee. Chaque validation expose dans
le snapshot public son identifiant, le fichier cible, son statut, sa progression
minimale, son erreur eventuelle et ses actions derivees. Les listes publiques
de fichiers et de validations sont des pages de 100 elements au plus;
`next_files_cursor` et `next_validations_cursor` permettent de poursuivre la
lecture sans fixer de limite au nombre de pistes ou de validations d'une
session. Le runner recoit pour sa part le seul chemin gere d'un manifeste de
sources, jamais la liste complete via IPC.
Une validation `failed` ou `cancel_failed` expose obligatoirement une erreur
publique; `cancel_failed` utilise le code `cancel_failed`. Les autres etats ne
portent pas d'erreur, afin que l'interface ne doive pas deviner si une
finalisation a echoue.
Un fichier `invalid` peut retourner vers `finalizing` par la commande
`retry_finalization`; cette action est exposee sur le snapshot du fichier vise.

## Jobs

`queued -> running -> completed|failed|timed_out` est le flux nominal. L'annulation est
`queued → cancelled` ou `running → cancel_requested → stopping → cancelled`;
un timeout d'arret donne `cancel_failed`. Lors d'un redemarrage, un job actif
(`running`, `cancel_requested`, `stopping`) devient `failed` avec le code
`server_interrupted`; les jobs `queued` ne sont pas echoues. Les statuts
terminaux peuvent expirer puis etre supprimes; les jobs actifs passent d'abord
par succes, echec, timeout ou annulation. Une relance cree une nouvelle
tentative et un nouveau job, jamais une transition du job terminal.

## Artefacts

`pending → ready` suit une ecriture atomique. Un echec donne `error`.
`ready|error → deleting → deleted`; une erreur de suppression retourne a
`error` pour reprise ulterieure. `deleted` est terminal.

## Actions publiques

Les actions sont calculees du snapshot : annuler, remplacer/supprimer un
fichier, relancer une finalisation, lancer, relancer a l'identique, modifier et
relancer, supprimer le job, regenerer le secret et consulter le resultat. Elles
guident l'interface seulement; la transition est toujours reverifiee cote
serveur avec la revision attendue.
