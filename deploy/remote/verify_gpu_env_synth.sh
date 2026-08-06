#!/usr/bin/env bash
set -euo pipefail

ROOT="${PEPAGENT_ROOT:-/sdd_data/pepagent}"
ENV_DIR="$ROOT/envs/gpu-worker-py311-v1"
UV="$ROOT/runtime/uv-0.11.12/bin/uv"
REPORT_DIR="$ROOT/runs/gpu-env-bootstrap-v1"

"$ENV_DIR/bin/python" - <<'PY'
from importlib.metadata import version

import boltz
import temporalio
import torch
import transformers

print("torch", torch.__version__, "cuda", torch.cuda.is_available())
print("transformers", transformers.__version__)
print("temporalio", version("temporalio"))
print("boltz", version("boltz"), boltz.__file__)
PY

help_text="$($ENV_DIR/bin/boltz predict --help)"
for option in \
  --cache \
  --diffusion_samples \
  --recycling_steps \
  --sampling_steps \
  --use_msa_server \
  --use_potentials \
  --write_full_pae \
  --write_full_pde; do
  grep -q -- "$option" <<<"$help_text" || {
    printf 'missing required boltz option: %s\n' "$option" >&2
    exit 10
  }
done

mkdir -p "$REPORT_DIR"
"$UV" pip freeze --python "$ENV_DIR/bin/python" > "$REPORT_DIR/environment.txt"
sha256sum "$REPORT_DIR/environment.txt" > "$REPORT_DIR/environment.sha256"
date --iso-8601=seconds > "$REPORT_DIR/VERIFIED"
cat "$REPORT_DIR/environment.sha256"
