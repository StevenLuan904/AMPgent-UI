#!/usr/bin/env bash
set -euo pipefail

ROOT="/sdd_data/pepagent"
UV="$ROOT/runtime/uv-0.11.12/bin/uv"
PYTHON="$ROOT/runtime/python/cpython-3.11.10-linux-x86_64-gnu/bin/python3.11"
ENV_DIR="$ROOT/envs/gpu-worker-py311-v1"
LOG_DIR="$ROOT/runs/gpu-env-bootstrap-v1"
BOLTZ_REV="b1ebfc46ecf57f5414e0d1a6f9027bbb122c53bc"
INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"

mkdir -p "$ROOT/envs" "$ROOT/cache/uv" "$LOG_DIR"
exec > >(tee -a "$LOG_DIR/bootstrap.log") 2>&1
export UV_CACHE_DIR="$ROOT/cache/uv"

"$UV" venv --python "$PYTHON" "$ENV_DIR"
"$UV" pip install --python "$ENV_DIR/bin/python" --index-url "$INDEX" \
  "torch==2.6.0"
"$UV" pip install --python "$ENV_DIR/bin/python" --index-url "$INDEX" \
  "$ROOT/platform/current[pepmlm]"
"$UV" pip install --python "$ENV_DIR/bin/python" --index-url "$INDEX" \
  "git+https://github.com/jwohlwend/boltz.git@$BOLTZ_REV"

"$ENV_DIR/bin/python" - <<'PY'
import torch
import transformers
import temporalio
import boltz
from importlib.metadata import version

print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("transformers", transformers.__version__)
print("temporalio", version("temporalio"))
print("boltz", boltz.__file__)
PY
"$ENV_DIR/bin/boltz" --help >/dev/null
"$UV" pip freeze --python "$ENV_DIR/bin/python" > "$LOG_DIR/environment.txt"
sha256sum "$LOG_DIR/environment.txt" > "$LOG_DIR/environment.sha256"
date --iso-8601=seconds > "$LOG_DIR/SUCCEEDED"
