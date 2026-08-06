#!/usr/bin/env bash
set -euo pipefail

ROOT="${PEPAGENT_ROOT:-/sdd_data/pepagent}"
UV_VERSION="0.11.12"
UV_HOME="$ROOT/runtime/uv-$UV_VERSION"
INSTALLER="$ROOT/bootstrap/uv-installer-$UV_VERSION.sh"
LOG="$ROOT/runs/runtime-bootstrap-v1.log"

mkdir -p "$ROOT/bootstrap" "$ROOT/runtime" "$ROOT/runs" "$UV_HOME/bin"
exec > >(tee -a "$LOG") 2>&1

if [[ ! -s "$INSTALLER" ]]; then
  curl --proto '=https' --tlsv1.2 --fail --location --retry 8 \
    "https://releases.astral.sh/github/uv/releases/download/$UV_VERSION/uv-installer.sh" \
    --output "$INSTALLER"
fi
sha256sum "$INSTALLER"
UV_INSTALL_DIR="$UV_HOME/bin" sh "$INSTALLER" --no-modify-path
"$UV_HOME/bin/uv" --version
"$UV_HOME/bin/uv" python install 3.11.10 --install-dir "$ROOT/runtime/python"
"$ROOT/runtime/python/cpython-3.11.10-linux-x86_64-gnu/bin/python3.11" --version
