# Vocabulaire public stable

Les textes sont choisis par le frontend via `message_key`; les contrats ne
transportent jamais une exception, un chemin, un prompt, un log ou un detail de
provider. Un statut terminal de job et un `error_code` sont distincts. Ils
restent toutefois coherents : un job ou resultat `timed_out` porte
obligatoirement `timeout`, et `cancel_failed` porte obligatoirement
`cancel_failed`. Les autres echecs utilisent un code stable adapte a leur cause.

| Code | Message key | Parametres publics autorises |
|---|---|---|
| `input_invalid` | `errors.input_invalid` | `input_type` |
| `input_too_large` | `errors.input_too_large` | `input_type`, `limit` |
| `transcription_failed` | `errors.transcription_failed` | aucun |
| `processing_failed` | `errors.processing_failed` | aucun |
| `timeout` | `errors.timeout` | `stage` |
| `cancel_failed` | `errors.cancel_failed` | aucun |
| `server_interrupted` | `errors.server_interrupted` | aucun |
| `artifact_write_failed` | `errors.artifact_write_failed` | `artifact_type` |
| `result_integrity_failed` | `errors.result_integrity_failed` | aucun |

Les avertissements V1 sont `audio_duration_high`, `retry_in_progress`,
`artifact_missing` et `service_degraded`. Les huit etapes publiques sont
`queued`, `input_validation`, `transcription`, `session_preparation`,
`narrative_analysis`, `synthesis`, `verification` et `result_ready`.
Les actions publiques sont les valeurs de `AllowedAction`. Toute valeur
inconnue est rejetee, pas affichee par defaut.

## Code support des erreurs HTTP

Chaque réponse RFC 9457 contient un `correlation_id` opaque, indépendant du
secret de session ou de job. Le frontend conserve cet identifiant dans son
erreur typée et l'ajoute au message générique sous la forme
`Code support : <correlation_id>`. Il ne montre jamais le détail technique de
l'exception.

Les journaux d'accès structurés portent la même valeur sous la clé
`correlation_id`. Un administrateur doit rechercher une correspondance exacte,
sur la fenêtre temporelle signalée, sans demander à l'utilisateur son lien
privé ni ses fichiers. Une réponse produite par un proxy sans corps Problem
Details peut ne pas fournir de code support ; le message générique reste alors
le seul texte affiché.

Les erreurs terminales persistées dans un snapshot de job utilisent le tableau
ci-dessus et ne sont pas des réponses HTTP Problem Details. Elles ne reçoivent
donc pas artificiellement le `correlation_id` d'une lecture ultérieure du
snapshot.
