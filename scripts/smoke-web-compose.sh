#!/bin/sh
set -eu

command -v docker >/dev/null
command -v openssl >/dev/null
command -v curl >/dev/null

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
temporary=$(mktemp -d)
project="tara-smoke-$$"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    docker compose -p "$project" -f "$root/compose.yaml" \
      -f "$root/compose.override.yaml.example" ps -a >&2 || true
    docker compose -p "$project" -f "$root/compose.yaml" \
      -f "$root/compose.override.yaml.example" logs --no-color >&2 || true
  fi
  docker compose -p "$project" -f "$root/compose.yaml" \
    -f "$root/compose.override.yaml.example" \
    --profile operations --profile restore down -v --remove-orphans \
    >/dev/null 2>&1 || true
  rm -rf "$temporary"
  return "$status"
}
trap cleanup EXIT INT TERM

openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -keyout "$temporary/tls.key" -out "$temporary/tls.crt" >/dev/null 2>&1
openssl rand -hex 32 >"$temporary/backup-signing-key"
printf '%s' smoke-modal-id >"$temporary/modal-token-id"
printf '%s' smoke-modal-secret >"$temporary/modal-token-secret"
chmod 700 "$temporary"
# The private parent directory protects these ephemeral files on the host;
# read bits are required because local Compose file secrets retain host modes.
chmod 444 "$temporary"/*

export TARA_WEB_TLS_CERTIFICATE_FILE="$temporary/tls.crt"
export TARA_WEB_TLS_PRIVATE_KEY_FILE="$temporary/tls.key"
export TARA_WEB_BACKUP_KEY_FILE="$temporary/backup-signing-key"
export MODAL_TOKEN_ID_FILE="$temporary/modal-token-id"
export MODAL_TOKEN_SECRET_FILE="$temporary/modal-token-secret"
export TARA_WEB_HOST_PORT=18443

compose="docker compose -p $project -f $root/compose.yaml -f $root/compose.override.yaml.example"
# The arguments above contain only paths created by this script.
$compose config --quiet
$compose up --build -d

deadline=$(( $(date +%s) + 120 ))
until curl --fail --silent --show-error --insecure \
  https://127.0.0.1:18443/api/v1/ready >/dev/null; do
  [ "$(date +%s)" -lt "$deadline" ] || exit 1
  sleep 2
done

container=$($compose ps -q tara-web)
[ -n "$container" ]
[ "$(docker inspect -f '{{.Config.User}}' "$container")" = "10001:10001" ]
[ "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$container")" = true ]
case "$(docker inspect -f '{{json .HostConfig.PortBindings}}' "$container")" in
  null|'{}') ;;
  *) exit 1 ;;
esac
$compose exec -T tara-web sh -c \
  'test ! -e /var/run/docker.sock && test ! -w /app && test ! -e /host-home'

$compose stop -t 90 tara-web
deadline=$(( $(date +%s) + 90 ))
while [ "$(docker inspect -f '{{.State.Running}}' "$container")" = true ]; do
  [ "$(date +%s)" -lt "$deadline" ] || exit 1
  sleep 1
done

backup_path=$($compose --profile operations run --rm --no-deps -T tara-web-backup)
generation=$(basename "$(printf '%s' "$backup_path" | tr -d '\r')")
case "$generation" in
  backup-*) ;;
  *) echo "backup service did not return a generation" >&2; exit 1 ;;
esac

export TARA_RESTORE_GENERATION="$generation"
$compose --profile restore run --rm --no-deps -T tara-web-restore
$compose --profile restore run --rm --no-deps -T --entrypoint python \
  tara-web-restore -c 'import sqlite3
database = sqlite3.connect("/restore/runtime/db/tara-web.sqlite3")
assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
assert database.execute(
    "SELECT count(*) FROM operator_action_audit WHERE action = ?", ("restore",)
).fetchone()[0] == 1
'
