#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: start_v39_target_sequence_worker.sh <gpu-id> <instance> <release-sha256> <source-revision>" >&2
  exit 2
fi

GPU_ID="$1"
INSTANCE="$2"
EXPECTED_RELEASE="$3"
SOURCE_REVISION="$4"
ROOT="${PEPAGENT_ROOT:?PEPAGENT_ROOT is required}"
PHYSICAL_HOST="${PEPAGENT_PHYSICAL_HOST:?PEPAGENT_PHYSICAL_HOST is required}"

[[ "$PHYSICAL_HOST:$ROOT:$GPU_ID" = "192.168.99.19:/data1/huangyueshan/pepagent:2" ]] || {
  echo "placement is outside the frozen v39 target-sequence allowlist" >&2
  exit 22
}
[[ "$INSTANCE" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "invalid worker instance" >&2; exit 2; }
[[ "$EXPECTED_RELEASE" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid release SHA-256" >&2; exit 2; }
[[ "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid source revision" >&2; exit 2; }

RELEASE_DIR="$ROOT/platform/releases-v39-target/$EXPECTED_RELEASE"
PYTHON="$ROOT/envs/gpu-worker-py311-v1/bin/python"
MODEL="$ROOT/models/PepMLM-650M/898fca941a9057aebdd1a6164b5ee09a1a71780e"
MODEL_WEIGHTS="$MODEL/pytorch_model.bin"
EXPECTED_WEIGHTS="8a3225bca1f9acd9f701ca2e46597c12bab92320e32b68f380ddf3b6d3b20770"

[[ -d "$RELEASE_DIR" ]] || { echo "immutable release directory is missing" >&2; exit 3; }
SCRIPT_RELEASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[[ "$SCRIPT_RELEASE_DIR" = "$RELEASE_DIR" ]] || {
  echo "launcher is not executing from the expected immutable release" >&2
  exit 3
}
[[ "$(tr -d '[:space:]' < "$RELEASE_DIR/.pepagent-source-revision")" = "$SOURCE_REVISION" ]] || {
  echo "release source marker drifted" >&2
  exit 3
}
[[ -x "$PYTHON" && -f "$MODEL_WEIGHTS" ]] || { echo "managed PepMLM runtime is missing" >&2; exit 4; }
[[ "$(sha256sum "$MODEL_WEIGHTS" | cut -d ' ' -f 1)" = "$EXPECTED_WEIGHTS" ]] || {
  echo "PepMLM weights drifted" >&2
  exit 4
}

OCCUPANTS="$(nvidia-smi -i "$GPU_ID" --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | tr -d '[:space:]')"
[[ -z "$OCCUPANTS" ]] || { echo "GPU has compute processes; refusing launch" >&2; exit 21; }
for p in /proc/[0-9]*; do
  if { tr '\0' '\n' < "$p/environ"; } 2>/dev/null | grep -q "^CUDA_VISIBLE_DEVICES=$GPU_ID$"; then
    echo "GPU has a CUDA_VISIBLE_DEVICES declaration; refusing launch" >&2
    exit 21
  fi
done

"$PYTHON" - <<'PY'
import socket

services = {
    "PostgreSQL": ("127.0.0.1", 55432),
    "Temporal": ("127.0.0.1", 17233),
    "object store": ("127.0.0.1", 19000),
}
failures = []
for name, address in services.items():
    try:
        with socket.create_connection(address, timeout=3):
            pass
    except OSError as error:
        failures.append(f"{name} {address[0]}:{address[1]} ({error})")
if failures:
    raise SystemExit("v39 target-sequence tunnel preflight failed: " + "; ".join(failures))
PY

ENVIRONMENT_SHA256="$(env CUDA_VISIBLE_DEVICES="$GPU_ID" PYTHONPATH="$RELEASE_DIR/src" \
  PEPAGENT_PLATFORM_RELEASE_SHA256="$EXPECTED_RELEASE" "$PYTHON" -c \
  'from pepagent.provenance.environment import fingerprint_runtime; print(fingerprint_runtime()[0])')"
[[ "$ENVIRONMENT_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "environment fingerprint is invalid" >&2; exit 5; }

RUN_DIR="$ROOT/runs/workers/v39/v39-target-sequence/$INSTANCE"
PID_FILE="$RUN_DIR/worker.pid"
mkdir -p "$RUN_DIR" "$ROOT/work-v39"
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "worker instance is already running; replacement requires exact-ownership migration" >&2
  exit 20
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$RUN_DIR/worker-$STAMP.log"
nohup env \
  CUDA_VISIBLE_DEVICES="$GPU_ID" OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  PYTHONPATH="$RELEASE_DIR/src" PYTHONUNBUFFERED=1 \
  PEPAGENT_WORKER_ROLE=v39-target-sequence \
  PEPAGENT_WORKER_SOURCE_REVISION="$SOURCE_REVISION" \
  PEPAGENT_WORKER_PHYSICAL_HOST="$PHYSICAL_HOST" \
  PEPAGENT_WORKER_ROOT="$ROOT" PEPAGENT_WORKER_GPU_INDEX="$GPU_ID" \
  PEPAGENT_WORKER_ENVIRONMENT_SHA256="$ENVIRONMENT_SHA256" \
  PEPAGENT_WORKER_WEIGHTS_SHA256="$EXPECTED_WEIGHTS" \
  PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES=1 \
  PEPAGENT_PLATFORM_RELEASE_SHA256="$EXPECTED_RELEASE" \
  PEPAGENT_TEMPORAL_ADDRESS=127.0.0.1:17233 \
  PEPAGENT_S3_ENDPOINT=http://127.0.0.1:19000 \
  PEPAGENT_WORK_ROOT="$ROOT/work-v39" \
  PEPAGENT_PEPMLM_MODEL_PATH="$MODEL" \
  PEPAGENT_PEPMLM_MODEL_REVISION=898fca941a9057aebdd1a6164b5ee09a1a71780e \
  PEPAGENT_PEPMLM_WEIGHTS_SHA256="$EXPECTED_WEIGHTS" \
  "$PYTHON" -m pepagent.workers.v38_temporal_worker \
  >"$LOG_FILE" 2>&1 </dev/null &
PID="$!"
printf '%s\n' "$PID" >"$PID_FILE"
printf '%s\n' "$LOG_FILE" >"$RUN_DIR/latest-log"
printf '%s\n' \
  "schema=v39.target-sequence-worker-receipt.1" \
  "ampgent_owned=true" \
  "foreign=false" \
  "role=v39-target-sequence" \
  "task_queue=pepagent-gpu-target-sequence-v39" \
  "pid=$PID" \
  "resource=$GPU_ID" \
  "release_sha256=$EXPECTED_RELEASE" \
  "source_revision=$SOURCE_REVISION" \
  "environment_sha256=$ENVIRONMENT_SHA256" \
  "weights_sha256=$EXPECTED_WEIGHTS" >"$RUN_DIR/worker.receipt"
echo "started role=v39-target-sequence instance=$INSTANCE resource=$GPU_ID pid=$PID release=$EXPECTED_RELEASE revision=$SOURCE_REVISION environment=$ENVIRONMENT_SHA256"
