# Tara Evidence Skill

This skill supports on-demand transcript excerpt retrieval for Tara specialist
agents running through Cursor CLI.

## Script

`scripts/get_excerpt.py` reads a merged transcription artifact by absolute path
and prints only the segments overlapping a timestamp range.

## Integration

Tara enables the tool block in specialist prompts when
`analysis.llm.cursor_cli_specialist_tool` is set to `true`. The default is
`false` so baseline quality remains unchanged until the feature is validated.

## Example

```bash
python scripts/get_excerpt.py \
  --transcription "C:/path/to/merged_transcription.yaml" \
  --start 600 \
  --end 720 \
  --pad 20
```
