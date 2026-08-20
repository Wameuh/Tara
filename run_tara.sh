#!/usr/bin/env bash
set -euo pipefail

# Launches the local transcription inference server and standalone Tara on
# Linux/Ubuntu, or uses a configured remote inference endpoint.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUDIO_DIR=""
MERGED_TRANSCRIPTION=""
CONFIG=""
CONTEXT=""
PRIOR_CONTEXT=""
WRITE_CONTEXT_DEBUG=""
SKIP_ANALYSIS=""
INFERENCE_ENDPOINT=""
INFERENCE_AUTH_PROVIDER=""
USE_MODAL="0"
FORCE_RESTART_SERVER="0"
SERVER_PORT="8000"
SERVER_HOST="localhost"
LOG_FILE="${SCRIPT_DIR}/inference_server.log"
SERVER_PID=""
SERVER_STARTED_BY_US="0"
DEFAULT_CONFIG="${SCRIPT_DIR}/config/configuration.json"
PYTHON_EXE="${PYTHON_EXE:-python3}"
RUN_STARTED_DATE_UTC="$(date -u +%F)"

usage() {
  cat <<'EOF'
Usage: run_tara.sh [--audio-dir PATH | --merged-transcription FILE] [options]

Options:
  --config PATH              Path to configuration JSON
  --context PATH             General campaign context markdown/text
  --prior-context PATH       Previous-session context markdown/text
  --write-context-debug      Write redacted context debug artifact
  --skip-analysis            Run transcription and processing only
  --transcription-only       Alias for --skip-analysis
  --modal                    Use Modal inference and never start local inference
  --inference-endpoint URL   Remote or local inference endpoint
  --inference-auth-provider PROVIDER
                             Inference auth provider: none, modal_map, or modal_proxy
  --restart-server           Stop any server on the port and start a fresh one
  --server-port PORT         Inference server port for audio runs (default: 8000)
  --server-host HOST         Inference server host for audio runs (default: localhost)
  --inference-log PATH       Inference server log path (default: inference_server.log)
  -h, --help                 Show this help
EOF
}

load_dotenv() {
  local dotenv_path="${SCRIPT_DIR}/.env"
  [[ -f "$dotenv_path" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    local key="${line%%=*}"
    local value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ -n "${!key+x}" ]] && continue
    export "$key=$value"
  done <"$dotenv_path"
}

json_config_value() {
  local path="$1"
  local dotted_key="$2"
  "$PYTHON_EXE" -c 'import json, sys
path, dotted = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path, encoding="utf-8"))
    value = data
    for part in dotted.split("."):
        value = value[part]
except Exception:
    value = ""
if value is None:
    value = ""
print(value)
' "$path" "$dotted_key"
}

is_local_endpoint() {
  case "${1,,}" in
    http://localhost*|https://localhost*|http://127.0.0.1*|https://127.0.0.1*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

check_health() {
  "$PYTHON_EXE" -c 'import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
' "$1"
}

find_server_pid() {
  pgrep -f "uvicorn inference_server.app:app.*--port ${SERVER_PORT}" | head -n 1 || true
}

cleanup() {
  if [[ "$SERVER_STARTED_BY_US" == "1" && -n "$SERVER_PID" ]]; then
    echo
    echo "Stopping inference server (PID: ${SERVER_PID})..."
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}

print_modal_billing_hint() {
  [[ "$USE_MODAL" == "1" ]] || return 0
  local end_date
  end_date="$(date -u -d "${RUN_STARTED_DATE_UTC} + 1 day" +%F 2>/dev/null || date -u +%F)"
  echo
  echo "Modal billing report (UTC day covering this run):"
  if ! uv run --extra deploy modal billing report \
    --start "$RUN_STARTED_DATE_UTC" \
    --end "$end_date" \
    --resolution h \
    --tz local; then
    echo "Unable to fetch Modal billing report automatically. You can run:"
    echo "uv run --extra deploy modal billing report --start ${RUN_STARTED_DATE_UTC} --end ${end_date} --resolution h --tz local"
  fi
  echo "Note: Modal reports complete billing intervals; the newest usage may appear after Modal finalizes billing data."
}

ensure_inference_server() {
  local health_host="$SERVER_HOST"
  if [[ "$health_host" == "0.0.0.0" ]]; then
    health_host="127.0.0.1"
  fi
  HEALTH_URL="http://${health_host}:${SERVER_PORT}/health"

  if [[ "$FORCE_RESTART_SERVER" == "1" ]]; then
    local existing_pid
    existing_pid="$(find_server_pid)"
    if [[ -n "$existing_pid" ]]; then
      echo "Stopping existing inference server (PID: ${existing_pid})..."
      kill "$existing_pid" >/dev/null 2>&1 || true
      sleep 2
    fi
  fi

  if [[ "$FORCE_RESTART_SERVER" == "0" ]] && check_health "$HEALTH_URL"; then
    SERVER_PID="$(find_server_pid)"
    if [[ -n "$SERVER_PID" ]]; then
      echo "Reusing inference server on ${SERVER_HOST}:${SERVER_PORT} (PID: ${SERVER_PID})."
      return 0
    fi
  fi

  echo "Starting inference server on ${SERVER_HOST}:${SERVER_PORT}..."
  echo "Using Python: ${PYTHON_EXE}"
  echo "Inference server log: ${LOG_FILE}"
  (
    cd "$SCRIPT_DIR"
    "$PYTHON_EXE" -m uvicorn inference_server.app:app \
      --host "$SERVER_HOST" \
      --port "$SERVER_PORT" \
      >"$LOG_FILE" 2>&1
  ) &
  SERVER_PID="$!"
  SERVER_STARTED_BY_US="1"

  echo "Waiting for inference server to be ready..."
  for attempt in $(seq 1 30); do
    sleep 1
    if check_health "$HEALTH_URL"; then
      echo "Inference server is ready."
      return 0
    fi
    if (( attempt % 5 == 0 )); then
      echo "Still waiting for server... (attempt ${attempt}/30)"
    fi
  done

  echo "Error: inference server failed to start within 30 attempts." >&2
  echo "Check log: ${LOG_FILE}" >&2
  return 1
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

load_dotenv

while [[ $# -gt 0 ]]; do
  case "$1" in
    --audio-dir) AUDIO_DIR="${2:-}"; shift 2 ;;
    --merged-transcription) MERGED_TRANSCRIPTION="${2:-}"; shift 2 ;;
    --config) CONFIG="${2:-}"; shift 2 ;;
    --context) CONTEXT="${2:-}"; shift 2 ;;
    --prior-context) PRIOR_CONTEXT="${2:-}"; shift 2 ;;
    --write-context-debug) WRITE_CONTEXT_DEBUG="1"; shift ;;
    --skip-analysis|--transcription-only) SKIP_ANALYSIS="1"; shift ;;
    --modal)
      USE_MODAL="1"
      INFERENCE_AUTH_PROVIDER="modal_map"
      export TARA_TRANSCRIPTION_PARALLELISM="${TARA_TRANSCRIPTION_PARALLELISM:-all}"
      shift
      ;;
    --inference-endpoint) INFERENCE_ENDPOINT="${2:-}"; shift 2 ;;
    --inference-auth-provider) INFERENCE_AUTH_PROVIDER="${2:-}"; shift 2 ;;
    --restart-server) FORCE_RESTART_SERVER="1"; shift ;;
    --server-port) SERVER_PORT="${2:-}"; shift 2 ;;
    --server-host) SERVER_HOST="${2:-}"; shift 2 ;;
    --inference-log) LOG_FILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$CONFIG" && -f "$DEFAULT_CONFIG" ]]; then
  CONFIG="$DEFAULT_CONFIG"
fi

if [[ -z "$AUDIO_DIR" && -z "$MERGED_TRANSCRIPTION" ]]; then
  echo "Error: --audio-dir or --merged-transcription is required." >&2
  usage
  exit 1
fi
if [[ -n "$AUDIO_DIR" && -n "$MERGED_TRANSCRIPTION" ]]; then
  echo "Error: use only one of --audio-dir or --merged-transcription." >&2
  exit 1
fi

if ! command -v "$PYTHON_EXE" >/dev/null 2>&1; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_EXE="python"
  else
    echo "Error: Python not found." >&2
    exit 1
  fi
fi

if [[ -n "$CONFIG" ]]; then
  worker_timeout="$(json_config_value "$CONFIG" "transcription.request_timeout_seconds")"
  [[ -n "$worker_timeout" ]] && export INFERENCE_WORKER_TIMEOUT_SECONDS="$worker_timeout"
  if [[ -z "$INFERENCE_ENDPOINT" ]]; then
    INFERENCE_ENDPOINT="$(json_config_value "$CONFIG" "transcription.inference_endpoint")"
  fi
  if [[ -z "$INFERENCE_AUTH_PROVIDER" ]]; then
    INFERENCE_AUTH_PROVIDER="$(json_config_value "$CONFIG" "transcription.inference_auth_provider")"
  fi
fi

if [[ "$USE_MODAL" == "1" ]]; then
  INFERENCE_AUTH_PROVIDER="modal_map"
fi
[[ -n "$INFERENCE_ENDPOINT" ]] && export TARA_INFERENCE_ENDPOINT="$INFERENCE_ENDPOINT"
[[ -n "$INFERENCE_AUTH_PROVIDER" ]] && export TARA_INFERENCE_AUTH_PROVIDER="$INFERENCE_AUTH_PROVIDER"

if [[ "${TARA_INFERENCE_AUTH_PROVIDER:-}" == "modal_map" ]]; then
  USE_MODAL="1"
fi
if [[ "${TARA_INFERENCE_AUTH_PROVIDER:-}" == "modal_proxy" && "${TARA_INFERENCE_ENDPOINT:-}" == *".modal.run"* ]]; then
  echo "Upgrading legacy Modal HTTP configuration to modal_map (parallel Function.spawn)."
  export TARA_INFERENCE_AUTH_PROVIDER="modal_map"
  USE_MODAL="1"
  export TARA_TRANSCRIPTION_PARALLELISM="${TARA_TRANSCRIPTION_PARALLELISM:-all}"
fi

export PYTHONPATH="${SCRIPT_DIR}/src:${PYTHONPATH:-}"

trap cleanup EXIT

if [[ -n "$AUDIO_DIR" ]]; then
  if [[ ! -d "$AUDIO_DIR" ]]; then
    echo "Error: Audio directory does not exist: ${AUDIO_DIR}" >&2
    exit 1
  fi
  AUDIO_DIR="$(cd "$AUDIO_DIR" && pwd)"
fi

if [[ -n "$MERGED_TRANSCRIPTION" ]]; then
  if [[ ! -f "$MERGED_TRANSCRIPTION" ]]; then
    echo "Error: Merged transcription file does not exist: ${MERGED_TRANSCRIPTION}" >&2
    exit 1
  fi
  MERGED_TRANSCRIPTION="$(cd "$(dirname "$MERGED_TRANSCRIPTION")" && pwd)/$(basename "$MERGED_TRANSCRIPTION")"
fi

if [[ -n "$AUDIO_DIR" ]]; then
  if [[ "$USE_MODAL" == "1" ]]; then
    echo "Using Modal parallel transcription (modal_map / Function.spawn)."
    "$PYTHON_EXE" -m tara.modal_preflight
  elif [[ -n "${TARA_INFERENCE_ENDPOINT:-}" ]]; then
    if is_local_endpoint "$TARA_INFERENCE_ENDPOINT"; then
      ensure_inference_server
      export TARA_INFERENCE_ENDPOINT="${HEALTH_URL%/health}"
    else
      echo "Using remote inference endpoint: ${TARA_INFERENCE_ENDPOINT}"
    fi
  else
    ensure_inference_server
    export TARA_INFERENCE_ENDPOINT="${HEALTH_URL%/health}"
  fi
fi

TARA_ARGS=()
[[ -n "$AUDIO_DIR" ]] && TARA_ARGS+=(--audio-dir "$AUDIO_DIR")
[[ -n "$MERGED_TRANSCRIPTION" ]] && TARA_ARGS+=(--merged-transcription "$MERGED_TRANSCRIPTION")
[[ -n "$CONFIG" ]] && TARA_ARGS+=(--config "$CONFIG")
[[ -n "$CONTEXT" ]] && TARA_ARGS+=(--context "$CONTEXT")
[[ -n "$PRIOR_CONTEXT" ]] && TARA_ARGS+=(--prior-context "$PRIOR_CONTEXT")
[[ -n "$WRITE_CONTEXT_DEBUG" ]] && TARA_ARGS+=(--write-context-debug)
[[ -n "$SKIP_ANALYSIS" ]] && TARA_ARGS+=(--skip-analysis)

echo
echo "Starting standalone Tara..."
echo "Using Python: ${PYTHON_EXE}"
set +e
"$PYTHON_EXE" -m tara "${TARA_ARGS[@]}"
TARA_EXIT_CODE="$?"
set -e
print_modal_billing_hint
exit "$TARA_EXIT_CODE"
