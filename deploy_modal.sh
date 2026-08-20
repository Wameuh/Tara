#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "Deploying Tara Modal inference app..."
uv run --extra deploy modal deploy modal_inference.py

echo "Warming Parakeet model cache on Modal volume tara-parakeet-cache..."
if ! uv run --extra deploy modal run modal_inference.py::warm_cache_entrypoint; then
  echo "WARNING: warm_cache failed. Endpoint is deployed but cache may be cold." >&2
  echo "Retry: uv run --extra deploy modal run modal_inference.py::warm_cache_entrypoint" >&2
  exit 0
fi

echo "Modal deploy and warm cache completed successfully."
