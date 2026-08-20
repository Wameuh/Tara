# Scene analysis prompts and artifacts

Scene pipeline prompts embed structured context as **YAML** (via `tara.yaml_utils`)
to reduce LLM token usage. LLM outputs are parsed as YAML through
`parse_typed_yaml` in `structured_output.py`.

Runtime artifacts use `.yaml` extensions:

- `scene_analysis.yaml`
- `scene_descriptions.yaml`
- `scenes/scene_001.yaml`

Cache helpers in `cache.py` read and write YAML while keeping `stable_json_hash`
for internal fingerprinting.
