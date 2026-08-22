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

Prompt-injection screening is configured under `analysis.prompt_security`:

- `enabled`: require the Cursor CLI safety gate before analysis.
- `minimum_score`: minimum accepted score on the 0-100 scale.
- `max_chars_per_request`: bounded chunk size for each Cursor request.
- `model`: Cursor model, or `Auto` for Cursor routing.

The gate reuses `analysis.llm.cursor_command`, `cursor_args`, timeout, retries,
and pricing. When enabled it is fail-closed: unavailable Cursor or an invalid
verdict refuses the job.

## Ko-fi monthly funding display

The web interface can display two monthly bars: Ko-fi donations received and
the cumulative costs estimated by Tara. Enable `webinterface.kofi`, configure
the public Ko-fi page URL and timezone, then set the private
`TARA_KOFI_VERIFICATION_TOKEN` environment variable. In Ko-fi, use this webhook
URL:

`https://YOUR-TARA-HOST/api/v1/funding/kofi/webhook`

Only donation and subscription payments in EUR contribute to the donation
bar. Shop orders are excluded. Tara stores the Ko-fi message identifier,
event type, amount, currency and timestamps only; supporter names, emails and
messages are discarded. Duplicate webhook deliveries are counted once.

`monthly_goal_micro_eur` is optional and sets the common scale of both bars
(`50000000` means EUR 50). Without it, the interface scales both bars to the
largest value in the current month. Tracking starts when the webhook is
enabled; Ko-fi does not provide a read API for importing earlier payments.
