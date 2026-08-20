#!/bin/sh
set -eu
umask 077
[ -n "${TARA_RESTORE_GENERATION:-}" ] || { echo "restore generation is required" >&2; exit 64; }
[ -n "${TARA_WEB_BACKUP_SIGNING_KEY_FILE:-}" ] || { echo "backup key file is required" >&2; exit 78; }
case "$TARA_RESTORE_GENERATION" in
  backup-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z-*) ;;
  *) echo "restore generation name is invalid" >&2; exit 64 ;;
esac
token=${TARA_RESTORE_GENERATION##*-}
case "$token" in
  *[!0-9a-f]*) echo "restore generation name is invalid" >&2; exit 64 ;;
esac
[ "${#token}" -eq 32 ] || { echo "restore generation name is invalid" >&2; exit 64; }
[ -f "$TARA_WEB_BACKUP_SIGNING_KEY_FILE" ] || { echo "backup key file is unavailable" >&2; exit 78; }
export TARA_WEB_BACKUP_SIGNING_KEY="$(cat "$TARA_WEB_BACKUP_SIGNING_KEY_FILE")"
[ -n "$TARA_WEB_BACKUP_SIGNING_KEY" ] || { echo "backup key file is empty" >&2; exit 78; }
exec python -m tara_web.cli --config "${TARA_WEB_CONFIG:-/config/webinterface.yaml}" restore "/data/backups/$TARA_RESTORE_GENERATION"
