#!/bin/sh
set -eu
umask 077
[ -n "${TARA_WEB_BACKUP_SIGNING_KEY_FILE:-}" ] || { echo "backup key file is required" >&2; exit 78; }
[ -f "$TARA_WEB_BACKUP_SIGNING_KEY_FILE" ] || { echo "backup key file is unavailable" >&2; exit 78; }
export TARA_WEB_BACKUP_SIGNING_KEY="$(cat "$TARA_WEB_BACKUP_SIGNING_KEY_FILE")"
[ -n "$TARA_WEB_BACKUP_SIGNING_KEY" ] || { echo "backup key file is empty" >&2; exit 78; }
if [ -n "${TARA_KOFI_VERIFICATION_TOKEN_FILE:-}" ]; then
  [ -f "$TARA_KOFI_VERIFICATION_TOKEN_FILE" ] || { echo "Ko-fi token file is unavailable" >&2; exit 78; }
  export TARA_KOFI_VERIFICATION_TOKEN="$(cat "$TARA_KOFI_VERIFICATION_TOKEN_FILE")"
  [ -n "$TARA_KOFI_VERIFICATION_TOKEN" ] || { echo "Ko-fi token file is empty" >&2; exit 78; }
  unset TARA_KOFI_VERIFICATION_TOKEN_FILE
fi
exec python -m tara_web.cli --config "${TARA_WEB_CONFIG:-/config/webinterface.yaml}" migrate
