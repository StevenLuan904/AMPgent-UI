#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: start_worker_synth.sh <pepmlm|boltz2|rosetta> <gpu-id|cpu> [instance]" >&2
  exit 2
fi
ROLE="$1"
GPU_ID="$2"
INSTANCE="${3:-primary}"
[[ "$INSTANCE" =~ ^[A-Za-z0-9_-]+$ ]] || {
  echo "worker instance must contain only letters, digits, underscore or dash" >&2
  exit 2
}
case "$ROLE" in
  pepmlm|boltz2) ;;
  rosetta) ;;
  *) echo "unsupported worker role: $ROLE" >&2; exit 2 ;;
esac
if [[ "$ROLE" = "rosetta" ]]; then
  [[ "$GPU_ID" = "cpu" ]] || { echo "Rosetta worker resource must be cpu" >&2; exit 2; }
  MAX_CONCURRENT="${PEPAGENT_ROSETTA_CONCURRENCY:-1}"
  [[ "$MAX_CONCURRENT" =~ ^[1-9][0-9]*$ ]] || {
    echo "PEPAGENT_ROSETTA_CONCURRENCY must be a positive integer" >&2
    exit 2
  }
else
  [[ "$GPU_ID" =~ ^[0-9]+$ ]] || { echo "GPU ID must be a non-negative integer" >&2; exit 2; }
  nvidia-smi -i "$GPU_ID" --query-gpu=index --format=csv,noheader,nounits >/dev/null 2>&1 || {
    echo "GPU ID does not exist: $GPU_ID" >&2
    exit 2
  }
  OCCUPANTS="$(nvidia-smi -i "$GPU_ID" --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | tr -d '[:space:]')"
  [[ -z "$OCCUPANTS" ]] || {
    echo "GPU $GPU_ID already has compute processes; refusing to start worker" >&2
    exit 21
  }
  MAX_CONCURRENT=1
fi

ROOT="${PEPAGENT_ROOT:-/sdd_data/pepagent}"
if [[ "$ROLE" = "rosetta" ]]; then
  PYTHON="$ROOT/envs/pyrosetta-quarterly-py311-v1/bin/python"
  CUDA_DEVICE=""
else
  PYTHON="$ROOT/envs/gpu-worker-py311-v1/bin/python"
  CUDA_DEVICE="$GPU_ID"
fi
RELEASE_SHA256="$(basename "$(readlink -f "$ROOT/platform/current")")"
[[ "$RELEASE_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "active platform release is not content-addressed: $RELEASE_SHA256" >&2
  exit 3
}
RUN_DIR="$ROOT/runs/workers/$ROLE/$INSTANCE"
PID_FILE="$RUN_DIR/worker.pid"
mkdir -p "$RUN_DIR" "$ROOT/work" "$ROOT/models/boltz2/cache"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "worker already running: role=$ROLE pid=$(cat "$PID_FILE")" >&2
  exit 20
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$RUN_DIR/worker-$STAMP.log"
nohup env \
  CUDA_VISIBLE_DEVICES="$CUDA_DEVICE" \
  PYTHONPATH="$ROOT/platform/current/src" \
  PYTHONUNBUFFERED=1 \
  PEPAGENT_WORKER_ROLE="$ROLE" \
  PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES="$MAX_CONCURRENT" \
  PEPAGENT_PLATFORM_RELEASE_SHA256="$RELEASE_SHA256" \
  PEPAGENT_TEMPORAL_ADDRESS=127.0.0.1:17233 \
  PEPAGENT_S3_ENDPOINT=http://127.0.0.1:19000 \
  PEPAGENT_WORK_ROOT="$ROOT/work" \
  PEPAGENT_PEPMLM_MODEL_PATH="$ROOT/models/PepMLM-650M/898fca941a9057aebdd1a6164b5ee09a1a71780e" \
  PEPAGENT_BOLTZ2_CACHE_PATH="$ROOT/models/boltz2/cache" \
  "$PYTHON" -m pepagent.workers.temporal_worker \
  >"$LOG_FILE" 2>&1 </dev/null &
PID="$!"
printf '%s\n' "$PID" > "$PID_FILE"
printf '%s\n' "$LOG_FILE" > "$RUN_DIR/latest-log"
echo "started role=$ROLE instance=$INSTANCE gpu=$GPU_ID pid=$PID log=$LOG_FILE"
