# Tara configuration

Default configuration lives in `configuration.yaml` (comments preserved via
`ruamel.yaml` round-trip mode). Legacy `configuration.json` files remain
readable through `load_config()`.

Key defaults now use YAML artifact names:

For Compose deployments, `tara_internal` uses the fixed `172.29.0.0/29`
network. Configure that exact CIDR in `webinterface.security.trusted_proxy_networks`;
leave the list empty for direct development servers.

- `processing.output_filename`: `merged_transcription.yaml`
- `analysis.summary_json_filename`: `session_summary.yaml` (field name kept for compatibility)
- `analysis.scenes.boundaries_filename`: `scene_analysis.yaml`
- `analysis.scenes.descriptions_filename`: `scene_descriptions.yaml`
