# Tara Evidence Excerpt Skill

Use this skill during Tara specialist extraction when the provided evidence
chunks are insufficient to support a factual claim.

## When to use

- You already received BM25 seed chunks in the prompt.
- You need additional transcript context around a specific timestamp range.
- You must cite transcript evidence, not invent facts.

## How to fetch an excerpt

Run the bundled script with absolute paths:

```bash
python ".cursor/skills/tara-evidence/scripts/get_excerpt.py" \
  --transcription "C:/absolute/path/to/merged_transcription.yaml" \
  --start 1200.0 \
  --end 1260.0 \
  --pad 15
```

The script prints compact rows:

```text
[42 1198.4-1205.2s wameuh] Example utterance.
```

## Rules

- Keep the seed chunks authoritative.
- Use the tool only to add missing context for a specific event or timestamp.
- Do not re-read the entire transcript when a narrow range is enough.
- Every extracted fact must still cite a supporting chunk id from the prompt,
  or clearly mark uncertainty when only excerpt context supports the claim.
