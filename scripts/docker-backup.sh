#!/bin/sh
set -eu
umask 077
[ -n "${TARA_WEB_BACKUP_SIGNING_KEY_FILE:-}" ] || { echo "backup key file is required" >&2; exit 78; }
[ -f "$TARA_WEB_BACKUP_SIGNING_KEY_FILE" ] || { echo "backup key file is unavailable" >&2; exit 78; }
export TARA_WEB_BACKUP_SIGNING_KEY="$(cat "$TARA_WEB_BACKUP_SIGNING_KEY_FILE")"
[ -n "$TARA_WEB_BACKUP_SIGNING_KEY" ] || { echo "backup key file is empty" >&2; exit 78; }
exec python -m tara_web.cli --config "${TARA_WEB_CONFIG:-/config/webinterface.yaml}" backup
