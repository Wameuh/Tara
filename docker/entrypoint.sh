#!/bin/sh
set -eu

umask 077

load_secret() {
  variable="$1"
  file_variable="${variable}_FILE"
  eval "secret_file=\${$file_variable:-}"
  if [ -n "$secret_file" ]; then
    [ -f "$secret_file" ] || { echo "secret file is unavailable" >&2; exit 78; }
    value=$(cat "$secret_file")
    [ -n "$value" ] || { echo "secret file is empty" >&2; exit 78; }
    export "$variable=$value"
    unset "$file_variable"
  fi
}

load_secret TARA_WEB_BACKUP_SIGNING_KEY
load_secret MODAL_TOKEN_ID
load_secret MODAL_TOKEN_SECRET
load_secret TARA_MODAL_PROXY_AUTH_KEY
load_secret TARA_MODAL_PROXY_AUTH_SECRET

config="${TARA_WEB_CONFIG:-/config/webinterface.yaml}"
python /app/scripts/docker_preflight.py --config "$config"

exec python -m tara_web.main "$@"
