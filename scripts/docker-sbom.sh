#!/bin/sh
set -eu

image="${1:?usage: docker-sbom.sh IMAGE OUTPUT}"
output="${2:?usage: docker-sbom.sh IMAGE OUTPUT}"
command -v syft >/dev/null || { echo "syft is required" >&2; exit 69; }
case "$output" in
  /*) ;;
  *) echo "SBOM output must be an absolute path" >&2; exit 64 ;;
esac
umask 077
exec syft "$image" -o "spdx-json=$output"
