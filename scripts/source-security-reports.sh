#!/bin/sh
set -eu

output="${1:?usage: source-security-reports.sh ABSOLUTE_OUTPUT_DIRECTORY}"
case "$output" in
  /*) ;;
  *) echo "report directory must be an absolute path" >&2; exit 64 ;;
esac

command -v docker >/dev/null || { echo "docker is required" >&2; exit 69; }
command -v git >/dev/null || { echo "git is required" >&2; exit 69; }

root=$(git rev-parse --show-toplevel)
source_root=$(mktemp -d "${TMPDIR:-/tmp}/tara-source-security.XXXXXX")
cleanup() {
  case "$source_root" in
    "${TMPDIR:-/tmp}"/tara-source-security.*) rm -rf -- "$source_root" ;;
    *) echo "refusing to remove unexpected temporary path" >&2 ;;
  esac
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$output"
umask 077
git -C "$root" archive --format=tar HEAD | tar -xf - -C "$source_root"

trivy_image='ghcr.io/aquasecurity/trivy:0.73.0@sha256:7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c'
run_trivy() {
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env TRIVY_CACHE_DIR=/tmp/trivy-cache \
    --volume "$source_root:/workspace:ro" \
    --volume "$output:/reports" \
    --workdir /workspace \
    "$trivy_image" "$@"
}

run_trivy fs --quiet --skip-db-update --skip-version-check \
  --format spdx-json --output /reports/python.spdx.json uv.lock
run_trivy fs --quiet --skip-db-update --skip-version-check \
  --format spdx-json --output /reports/npm.spdx.json \
  webinterface/frontend/package-lock.json
run_trivy fs --quiet --skip-check-update --skip-version-check \
  --scanners secret,misconfig --severity HIGH,CRITICAL --exit-code 1 \
  --format json --output /reports/source-security.json .

(
  cd "$output"
  sha256sum python.spdx.json npm.spdx.json source-security.json \
    > source-security.sha256
)
