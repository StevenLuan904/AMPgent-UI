#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "usage: $0 CACHE_DIR BOLTZ_EXECUTABLE GUARDED_SMOKE_SHA256" >&2
  exit 64
fi

CACHE_DIR="$1"
BOLTZ_EXECUTABLE="$2"
GUARDED_SMOKE_SHA256="$3"
WEIGHTS="$CACHE_DIR/boltz2_conf.ckpt"
MOLECULAR_ARCHIVE="$CACHE_DIR/mols.tar"
EXPECTED_WEIGHTS_SIZE=2286561469
EXPECTED_WEIGHTS_SHA=090e82ac8c92f5e943fa1b39e7410a44027bea7243c0bbb3caa67a77fc1428e1
EXPECTED_MOLS_SIZE=1855662080
EXPECTED_MOLS_SHA=39e076d96dbec6b4e86982bbda16f3a53a2a60c9bdc17828d88f6f9a0c7d1fd7

[[ -x "$BOLTZ_EXECUTABLE" ]] || { echo "Boltz executable is not runnable" >&2; exit 65; }
[[ -r "$WEIGHTS" && -r "$MOLECULAR_ARCHIVE" ]] || {
  echo "Boltz cache files are missing or unreadable" >&2
  exit 66
}
[[ "$GUARDED_SMOKE_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "guarded smoke SHA-256 is invalid" >&2
  exit 67
}

WEIGHTS_SIZE="$(stat -c %s "$WEIGHTS")"
WEIGHTS_SHA="$(sha256sum "$WEIGHTS" | cut -d ' ' -f 1)"
MOLS_SIZE="$(stat -c %s "$MOLECULAR_ARCHIVE")"
MOLS_SHA="$(sha256sum "$MOLECULAR_ARCHIVE" | cut -d ' ' -f 1)"
MOLECULE_FILE_COUNT="$(find "$CACHE_DIR/mols" -type f -name '*.pkl' -printf x | wc -c)"

[[ "$WEIGHTS_SIZE" == "$EXPECTED_WEIGHTS_SIZE" && "$WEIGHTS_SHA" == "$EXPECTED_WEIGHTS_SHA" ]] || {
  echo "Boltz confidence checkpoint identity mismatch" >&2
  exit 68
}
[[ "$MOLS_SIZE" == "$EXPECTED_MOLS_SIZE" && "$MOLS_SHA" == "$EXPECTED_MOLS_SHA" ]] || {
  echo "Boltz molecular archive identity mismatch" >&2
  exit 69
}
(( MOLECULE_FILE_COUNT > 0 )) || { echo "Boltz molecule cache is empty" >&2; exit 70; }

printf '{"schema_version":"v38.boltz-runtime-cache-attestation.1","boltz_executable":"%s","weights":{"filename":"boltz2_conf.ckpt","size_bytes":%s,"sha256":"%s"},"molecular_archive":{"filename":"mols.tar","size_bytes":%s,"sha256":"%s"},"molecule_file_count":%s,"guarded_smoke_sha256":"%s"}\n' \
  "$BOLTZ_EXECUTABLE" "$WEIGHTS_SIZE" "$WEIGHTS_SHA" "$MOLS_SIZE" "$MOLS_SHA" \
  "$MOLECULE_FILE_COUNT" "$GUARDED_SMOKE_SHA256"
