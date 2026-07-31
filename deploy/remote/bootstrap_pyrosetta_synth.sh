#!/usr/bin/env bash
set -euo pipefail

ROOT="/sdd_data/pepagent"
UV="$ROOT/runtime/uv-0.11.12/bin/uv"
PYTHON="$ROOT/runtime/python/cpython-3.11.10-linux-x86_64-gnu/bin/python3.11"
ENV_DIR="$ROOT/envs/pyrosetta-quarterly-py311-v1"
LOG_DIR="$ROOT/runs/pyrosetta-env-bootstrap-v1"
WHEEL="$ROOT/downloads/pyrosetta-2026.29+releasequarterly.80a0635615-cp311-cp311-linux_x86_64.whl"
WHEEL_SHA256="25254a10363eb5bdc0e1f3f36cbf846cb513958281041dd2b1b259610de2e733"
INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"

mkdir -p "$ROOT/envs" "$ROOT/cache/uv" "$LOG_DIR"
exec > >(tee -a "$LOG_DIR/bootstrap.log") 2>&1
export UV_CACHE_DIR="$ROOT/cache/uv"

test -f "$WHEEL"
ACTUAL_WHEEL_SHA256="$(sha256sum "$WHEEL" | awk '{print $1}')"
test "$ACTUAL_WHEEL_SHA256" = "$WHEEL_SHA256"
test -L "$ROOT/platform/current"

"$UV" venv --clear --python "$PYTHON" "$ENV_DIR"
"$UV" pip install --python "$ENV_DIR/bin/python" --index-url "$INDEX" "$WHEEL"
"$UV" pip install --python "$ENV_DIR/bin/python" --index-url "$INDEX" \
  "$ROOT/platform/current"

"$ENV_DIR/bin/python" - <<'PY'
from importlib.metadata import version

import pyrosetta

expected = "2026.29+releasequarterly.80a0635615"
actual = version("pyrosetta")
if actual != expected:
    raise SystemExit(f"unexpected PyRosetta release: {actual} != {expected}")
print("pyrosetta", actual)
print("pyrosetta_module", pyrosetta.__file__)
PY

"$UV" pip freeze --python "$ENV_DIR/bin/python" > "$LOG_DIR/environment.txt"
sha256sum "$LOG_DIR/environment.txt" > "$LOG_DIR/environment.sha256"
printf '%s\n' "$WHEEL_SHA256" > "$LOG_DIR/pyrosetta-wheel.sha256"
readlink -f "$ROOT/platform/current" > "$LOG_DIR/platform-release.txt"
date --iso-8601=seconds > "$LOG_DIR/SUCCEEDED"
