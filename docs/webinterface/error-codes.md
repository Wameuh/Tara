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
