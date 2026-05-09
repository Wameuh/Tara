# 04 - EvidenceIndexAgent et retrieval local

## Objectif

Construire un index local depuis `merged_transcription.json` afin que les LLM ne relisent jamais toute la transcription.

## A recuperer de Tara

- `src/tara/processing/models.py` pour comprendre le format des segments.
- `merged_transcription.json` comme entree officielle.
- `telemetry` pour mesurer nombre de chunks, taille indexee, temps de traitement.

## A ne pas recuperer tel quel

- `TranscriptionSplitterAgent`: le decoupage par scene n'est plus central.
- `SceneAnalyzerAgent`: ne plus demander au LLM de lire toute la transcription.

## Outputs

- `evidence_chunks.jsonl`
- `evidence_index.sqlite` ou index equivalent
- `evidence_index_metadata.json`

## Taches

1. Charger `merged_transcription.json`.
2. Creer des chunks glissants:
   - duree cible: 60 a 120 secondes;
   - overlap: 15 a 30 secondes;
   - conserver ids de segments et timestamps.
3. Ajouter metadonnees:
   - personnages detectes;
   - mots cles: soin, mort, fuite, ennemi, ressource, lieu, objet, mecanisme;
   - position temporelle.
4. Implementer retrieval lexical:
   - simple score token overlap au depart;
   - BM25 si dependance acceptable.
5. Ajouter embeddings locaux si disponible et non couteux:
   - optionnel;
   - fallback lexical obligatoire.
6. Exposer API:

```python
retrieve(query: RetrievalQuery, limit: int) -> list[RetrievedEvidence]
```

7. Ajouter un coverage report:
   - chunks crees;
   - duree couverte;
   - zones sans texte;
   - zones peu indexees.

## Criteres de validation

- `Record19` est indexe sans appel LLM.
- Chaque chunk pointe vers les segments originaux.
- Retrieval lexical retrouve des extraits pour des requetes simples comme "Molnir mort", "lance", "sanctuaire", "potion".
- L'index couvre toute la duree de la transcription.
