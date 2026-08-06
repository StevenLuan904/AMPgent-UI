#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[0-9a-f]{64}$ ]]; then
  echo "usage: activate_platform_release_synth.sh <archive-sha256>" >&2
  exit 2
fi

ROOT="${PEPAGENT_ROOT:-/sdd_data/pepagent}"
DIGEST="$1"
ARCHIVE="$ROOT/bootstrap/platform-$DIGEST.tar.gz"
RELEASE="$ROOT/platform/releases/$DIGEST"

test -f "$ARCHIVE"
ACTUAL="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
test "$ACTUAL" = "$DIGEST"
mkdir -p "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
ln -sfn "$RELEASE" "$ROOT/platform/current"
printf 'release=%s\nsha256=%s\n' "$(readlink -f "$ROOT/platform/current")" "$ACTUAL"
