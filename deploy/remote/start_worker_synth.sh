#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: start_worker_synth.sh <pepmlm|boltz2> <gpu-id>" >&2
  exit 2
fi
ROLE="$1"
GPU_ID="$2"
case "$ROLE" in
  pepmlm|boltz2) ;;
  *) echo "unsupported worker role: $ROLE" >&2; exit 2 ;;
esac
[[ "$GPU_ID" =~ ^[0-7]$ ]] || { echo "GPU ID must be 0-7" >&2; exit 2; }

ROOT="/sdd_data/pepagent"
PYTHON="$ROOT/envs/gpu-worker-py311-v1/bin/python"
RELEASE_SHA256="$(basename "$(readlink -f "$ROOT/platform/current")")"
[[ "$RELEASE_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "active platform release is not content-addressed: $RELEASE_SHA256" >&2
  exit 3
}
RUN_DIR="$ROOT/runs/workers/$ROLE"
PID_FILE="$RUN_DIR/worker.pid"
mkdir -p "$RUN_DIR" "$ROOT/work" "$ROOT/models/boltz2/cache"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "worker already running: role=$ROLE pid=$(cat "$PID_FILE")" >&2
  exit 20
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$RUN_DIR/worker-$STAMP.log"
nohup env \
  CUDA_VISIBLE_DEVICES="$GPU_ID" \
  PYTHONUNBUFFERED=1 \
  PEPAGENT_WORKER_ROLE="$ROLE" \
  PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES=1 \
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
echo "started role=$ROLE gpu=$GPU_ID pid=$PID log=$LOG_FILE"
