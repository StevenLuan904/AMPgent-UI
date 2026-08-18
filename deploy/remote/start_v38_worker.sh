#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: start_v38_worker.sh <v38-boltz|v38-rosetta> <gpu-id|cpu> <instance> <release-sha256> <source-revision>" >&2
  exit 2
fi

ROLE="$1"
RESOURCE="$2"
INSTANCE="$3"
EXPECTED_RELEASE="$4"
SOURCE_REVISION="$5"
ROOT="${PEPAGENT_ROOT:?PEPAGENT_ROOT is required}"
PHYSICAL_HOST="${PEPAGENT_PHYSICAL_HOST:?PEPAGENT_PHYSICAL_HOST is required}"

[[ "$INSTANCE" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "invalid worker instance" >&2; exit 2; }
[[ "$EXPECTED_RELEASE" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid release SHA-256" >&2; exit 2; }
[[ "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid source revision" >&2; exit 2; }

case "$PHYSICAL_HOST:$ROOT:$ROLE:$RESOURCE" in
  "192.168.99.19:/data1/huangyueshan/pepagent:v38-boltz:6") ;;
  "synth:/sdd_data/pepagent:v38-rosetta:cpu") ;;
  *) echo "placement is outside the frozen v38 allowlist" >&2; exit 22 ;;
esac

RELEASE_DIR="$ROOT/platform/releases-v38/$EXPECTED_RELEASE"
[[ -d "$RELEASE_DIR" ]] || { echo "expected immutable release directory is missing" >&2; exit 3; }
SCRIPT_RELEASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[[ "$SCRIPT_RELEASE_DIR" = "$RELEASE_DIR" ]] || {
  echo "launcher is not executing from the expected immutable release" >&2
  exit 3
}
[[ "$(tr -d '[:space:]' < "$RELEASE_DIR/.pepagent-source-revision")" = "$SOURCE_REVISION" ]] || {
  echo "v38 release source marker drifted" >&2
  exit 3
}

if [[ "$ROLE" = "v38-boltz" ]]; then
  PYTHON="$ROOT/envs/gpu-worker-py311-v1/bin/python"
  BOLTZ_EXECUTABLE="$ROOT/envs/gpu-worker-py311-v1/bin/boltz"
  CUDA_DEVICE="$RESOURCE"
  MAX_CONCURRENT=1
  [[ -x "$BOLTZ_EXECUTABLE" ]] || { echo "managed Boltz executable is missing" >&2; exit 4; }
  "$PYTHON" -c 'import boltz' || { echo "managed Boltz package is unavailable" >&2; exit 4; }
  BOLTZ_VERSION="$($PYTHON -c 'from importlib.metadata import version; print(version("boltz"))')"
  [[ "$BOLTZ_VERSION" = "2.2.1" ]] || {
    echo "managed Boltz version drifted" >&2
    exit 4
  }
  OCCUPANTS="$(nvidia-smi -i "$RESOURCE" --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | tr -d '[:space:]')"
  [[ -z "$OCCUPANTS" ]] || { echo "GPU has compute processes; refusing launch" >&2; exit 21; }
else
  PYTHON="$ROOT/envs/pyrosetta-quarterly-py311-v1/bin/python"
  CUDA_DEVICE=""
  MAX_CONCURRENT=16
fi
[[ -x "$PYTHON" ]] || { echo "managed worker Python is missing" >&2; exit 4; }

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
    raise SystemExit("v38 service tunnel preflight failed: " + "; ".join(failures))
PY

ENVIRONMENT_SHA256="$(env CUDA_VISIBLE_DEVICES="$CUDA_DEVICE" PYTHONPATH="$RELEASE_DIR/src" \
  PEPAGENT_PLATFORM_RELEASE_SHA256="$EXPECTED_RELEASE" "$PYTHON" -c \
  'from pepagent.provenance.environment import fingerprint_runtime; print(fingerprint_runtime()[0])')"
[[ "$ENVIRONMENT_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "environment fingerprint is invalid" >&2; exit 5; }
if [[ "$ROLE" = "v38-boltz" ]]; then
  WEIGHTS_SHA256="$(env PYTHONPATH="$RELEASE_DIR/src" "$PYTHON" -c \
    'from pepagent.settings import get_settings; print(get_settings().boltz2_weights_sha256)')"
  [[ "$WEIGHTS_SHA256" =~ ^[0-9a-f]{64}$ ]] || { echo "Boltz weights fingerprint is invalid" >&2; exit 5; }
else
  WEIGHTS_SHA256=""
fi

RUN_DIR="$ROOT/runs/workers/v38/$ROLE/$INSTANCE"
PID_FILE="$RUN_DIR/worker.pid"
mkdir -p "$RUN_DIR" "$ROOT/work" "$ROOT/models/boltz2/cache"
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "worker instance is already running; replacement requires external exact-ownership migration" >&2
  exit 20
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$RUN_DIR/worker-$STAMP.log"
nohup env \
  CUDA_VISIBLE_DEVICES="$CUDA_DEVICE" OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  PYTHONPATH="$RELEASE_DIR/src" PYTHONUNBUFFERED=1 \
  PEPAGENT_WORKER_ROLE="$ROLE" PEPAGENT_WORKER_SOURCE_REVISION="$SOURCE_REVISION" \
  PEPAGENT_WORKER_PHYSICAL_HOST="$PHYSICAL_HOST" PEPAGENT_WORKER_ROOT="$ROOT" \
  PEPAGENT_WORKER_GPU_INDEX="$RESOURCE" \
  PEPAGENT_WORKER_ENVIRONMENT_SHA256="$ENVIRONMENT_SHA256" \
  PEPAGENT_WORKER_WEIGHTS_SHA256="$WEIGHTS_SHA256" \
  PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES="$MAX_CONCURRENT" \
  PEPAGENT_PLATFORM_RELEASE_SHA256="$EXPECTED_RELEASE" \
  PEPAGENT_TEMPORAL_ADDRESS=127.0.0.1:17233 \
  PEPAGENT_S3_ENDPOINT=http://127.0.0.1:19000 \
  PEPAGENT_WORK_ROOT="$ROOT/work" \
  PEPAGENT_BOLTZ2_CACHE_PATH="$ROOT/models/boltz2/cache" \
  "$PYTHON" -m pepagent.workers.v38_temporal_worker \
  >"$LOG_FILE" 2>&1 </dev/null &
PID="$!"
printf '%s\n' "$PID" >"$PID_FILE"
printf '%s\n' "$LOG_FILE" >"$RUN_DIR/latest-log"
printf '%s\n' \
  "schema=v38.remote-worker-receipt.1" \
  "ampgent_owned=true" \
  "foreign=false" \
  "role=$ROLE" \
  "pid=$PID" \
  "resource=$RESOURCE" \
  "release_sha256=$EXPECTED_RELEASE" \
  "source_revision=$SOURCE_REVISION" \
  "environment_sha256=$ENVIRONMENT_SHA256" \
  "weights_sha256=$WEIGHTS_SHA256" >"$RUN_DIR/worker.receipt"
echo "started role=$ROLE instance=$INSTANCE resource=$RESOURCE pid=$PID release=$EXPECTED_RELEASE revision=$SOURCE_REVISION environment=$ENVIRONMENT_SHA256"
