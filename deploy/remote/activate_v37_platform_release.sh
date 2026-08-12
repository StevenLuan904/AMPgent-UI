#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 || ! "$1" =~ ^[0-9a-f]{64}$ || ! "$2" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: activate_v37_platform_release.sh <archive-sha256> <source-revision>" >&2
  exit 2
fi
DIGEST="$1"
SOURCE_REVISION="$2"
ROOT="${PEPAGENT_ROOT:?PEPAGENT_ROOT is required}"
PHYSICAL_HOST="${PEPAGENT_PHYSICAL_HOST:?PEPAGENT_PHYSICAL_HOST is required}"

case "$PHYSICAL_HOST:$ROOT" in
  "synth:/sdd_data/pepagent"|"192.168.99.19:/data1/huangyueshan/pepagent") ;;
  *) echo "deployment host/root is outside the v37 allowlist" >&2; exit 22 ;;
esac

ARCHIVE="$ROOT/bootstrap/platform-$DIGEST.tar.gz"
RELEASE="$ROOT/platform/releases/$DIGEST"
[[ -f "$ARCHIVE" ]] || { echo "content-addressed archive is missing" >&2; exit 3; }
ACTUAL="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
[[ "$ACTUAL" = "$DIGEST" ]] || { echo "archive SHA-256 mismatch" >&2; exit 3; }

if [[ -d "$RELEASE" ]]; then
  [[ -f "$RELEASE/.pepagent-source-revision" ]] || {
    echo "existing release lacks its source-revision binding" >&2
    exit 4
  }
  [[ "$(cat "$RELEASE/.pepagent-source-revision")" = "$SOURCE_REVISION" ]] || {
    echo "existing release belongs to another source revision" >&2
    exit 4
  }
else
  mkdir -p "$ROOT/platform/releases"
  STAGING="$(mktemp -d "$ROOT/platform/releases/.v37-$DIGEST.XXXXXX")"
  cleanup() { rm -rf -- "$STAGING"; }
  trap cleanup EXIT
  python3 - "$ARCHIVE" "$STAGING" <<'PY'
import os
import pathlib
import sys
import tarfile

archive, destination = sys.argv[1:]
with tarfile.open(archive, "r:gz") as handle:
    members = handle.getmembers()
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.isdev():
            raise SystemExit("unsafe archive member")
        if member.issym() or member.islnk():
            raise SystemExit("release archive may not contain links")
    handle.extractall(destination)
revision_path = os.path.join(destination, ".pepagent-source-revision")
if not os.path.isfile(revision_path):
    raise SystemExit("release archive lacks .pepagent-source-revision")
PY
  [[ "$(cat "$STAGING/.pepagent-source-revision")" = "$SOURCE_REVISION" ]] || {
    echo "archive source revision binding mismatch" >&2
    exit 4
  }
  mv -- "$STAGING" "$RELEASE"
  trap - EXIT
fi

CURRENT_TMP="$ROOT/platform/.current-v37-$DIGEST-$$"
ln -s "$RELEASE" "$CURRENT_TMP"
mv -Tf "$CURRENT_TMP" "$ROOT/platform/current"
echo "release=$RELEASE archive_sha256=$DIGEST source_revision=$SOURCE_REVISION"
