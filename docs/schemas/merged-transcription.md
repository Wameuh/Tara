# Merged transcription 26.0.1

Le document canonique a `schema_name: tara.merged_transcription` et `schema_version: 26.0.1`. Les quatre champs racine sont `schema_name`, `schema_version`, `metadata` et `content`; tout champ supplementaire est refuse.

`metadata` ne contient que des informations portables (`language`, `producer`, `created_at`, `labels`). `content` porte `text`, `segments`, `duration` et `model`. Un segment contient `start`, `end`, `text` et, eventuellement, `author`. Les horodatages sont finis, positifs et ordonnes.

`text` et `segments[].text` sont bornes par la constante `MAX_TRANSCRIPTION_TEXT_CHARS` a 32 MiB chacun. Cette borne structurelle ne remplace pas la limite produit de 500 000 tokens: celle-ci sera appliquee par le tokenizer partage de l'etape 10.

Les anciens documents non versionnes avec `text` et `segments`, y compris JSON, sont acceptes uniquement par l'adaptateur `legacy-v0` et migrent en memoire vers 26.0.1. Les autres versions sont refusees.

Le lecteur accepte un seul document YAML UTF-8 et borne octets, profondeur, noeuds, scalaires, alias et temps. Les tags explicites, cles dupliquees et scalaires implicites ambigus (`yes`, `no`, `on`, `off`) sont refuses.

```yaml
schema_name: tara.merged_transcription
schema_version: 26.0.1
metadata:
  language: fr
  producer: tara
content:
  text: "[ana] Le groupe entre dans la tour."
  segments:
    - start: 0.0
      end: 2.5
      text: Le groupe entre dans la tour.
      author:
        speaker: ana
        source_file: 1-ana.yaml
  duration: 2.5
  model: parakeet
```

| Profil | Octets | Noeuds | Scalaire | Alias | Temps |
|---|---:|---:|---:|---:|---:|
| merged transcription | 32 MiB | 1 000 000 | 32 MiB | 0 | 10 s |
| public result | 4 MiB | 2 000 000 | 100 000 caracteres | 0 | 3 s |

La borne merged couvre les 50 000 segments autorises, y compris les mappings
`author` et leurs cles. La borne publique est volontairement haute par rapport
aux 4 MiB: elle ne rejette donc pas un YAML compact uniquement a cause du
comptage de mappings ou de cles. La limite metier en tokens sera appliquee par
le tokenizer partage, pas par ce lecteur structurel.

Adaptateurs exacts: `tara.merged_transcription/legacy-v0` accepte uniquement la forme non versionnee `text`, `segments`, `language`, `duration`, `model`; les champs supplementaires sont refuses. Les JSON legacy sont bornes avant parsing et subissent les memes limites structurelles.
