# 02 - Couche commune LLM API/Cursor CLI

## Objectif

Creer une abstraction unique pour tous les appels LLM. Les agents ne doivent pas savoir si l'appel part vers une API ou vers Cursor CLI.

## A recuperer de Tara

- `src/tara/scene_analyzer/client.py` comme inspiration pour:
  - telemetry d'usage;
  - retries;
  - timeouts;
  - parsing usage tokens;
  - support provider OpenAI.
- `src/tara/usage_reporting/`
- `src/tara/telemetry/`
- configuration actuelle des modeles dans `config/configuration.json`.

## A ne pas recuperer tel quel

- le nom `analyze_scenes` du client actuel;
- la logique liee aux anciens agents scene;
- `max_output_tokens=200000` par defaut sans budget par usage;
- couplage direct OpenAI dans les agents.

## Fonctionnalite requise

Backends supportes:

- `api`: appel API LLM classique;
- `cursor_cli`: appel non-interactif via `agent -p`;

Modele par defaut:

- `Auto`.

Exemple de configuration cible:

```json
{
  "analysis": {
    "llm": {
      "backend": "cursor_cli",
      "model": "Auto",
      "cursor_command": "agent",
      "cursor_args": ["-p"],
      "timeout_seconds": 900,
      "max_retries": 2
    }
  }
}
```

## Taches

1. Creer `LLMRunner` avec une methode stable:

```python
run(prompt: LLMRequest) -> LLMResponse
```

2. Definir `LLMRequest`:
   - `purpose`
   - `system_prompt`
   - `user_prompt`
   - `response_format`
   - `model`
   - `temperature`
   - `max_output_tokens`
   - `metadata`
3. Definir `LLMResponse`:
   - `content`
   - `model`
   - `backend`
   - `input_tokens`
   - `output_tokens`
   - `total_tokens`
   - `raw_usage`
4. Implementer backend API:
   - reutiliser les idees de l'ancien client;
   - garder telemetry;
   - garder retries.
5. Implementer backend Cursor CLI:
   - construire la commande `agent -p`;
   - injecter le prompt complet en argument ou stdin selon le comportement retenu;
   - utiliser modele `Auto` par defaut;
   - capturer stdout/stderr;
   - timeout strict;
   - erreur lisible si `agent` n'est pas disponible.
6. Ajouter tests unitaires avec subprocess mocke.

## Questions techniques a trancher pendant implementation

- Cursor CLI accepte-t-il le modele via argument explicite ou seulement via configuration globale?
- Le prompt doit-il etre passe directement apres `-p` ou via stdin?
- Faut-il isoler les outputs temporaires pour les prompts longs?

## Criteres de validation

- Un agent peut appeler `LLMRunner` sans connaitre le backend.
- `backend=api` marche avec mock.
- `backend=cursor_cli` lance bien `agent -p` en test mocke.
- `model` vaut `Auto` si non configure.
- La telemetry enregistre `purpose`, backend, modele et usage disponible.
