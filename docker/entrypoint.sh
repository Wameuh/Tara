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

prepare_cursor_auth() {
  auth_file="${TARA_CURSOR_AUTH_FILE:-}"
  [ -n "$auth_file" ] || return 0
  [ -f "$auth_file" ] || { echo "Cursor auth file is unavailable" >&2; exit 78; }
  cursor_home=/tmp/cursor-home
  cursor_config="$cursor_home/.config/cursor"
  cursor_cache="$cursor_home/.cache"
  mkdir -p "$cursor_config" "$cursor_cache"
  cp "$auth_file" "$cursor_config/auth.json"
  chmod 700 "$cursor_home" "$cursor_home/.config" "$cursor_config" "$cursor_cache"
  chmod 600 "$cursor_config/auth.json"
  export HOME="$cursor_home"
  export XDG_CONFIG_HOME="$cursor_home/.config"
  export XDG_CACHE_HOME="$cursor_cache"
  export AGENT_CLI_CREDENTIAL_STORE=file
  unset TARA_CURSOR_AUTH_FILE
}

prepare_cursor_auth

config="${TARA_WEB_CONFIG:-/config/webinterface.yaml}"
python /app/scripts/docker_preflight.py --config "$config"

exec python -m tara_web.main "$@"
