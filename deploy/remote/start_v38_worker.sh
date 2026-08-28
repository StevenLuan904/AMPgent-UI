#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: start_v38_worker.sh <v38-boltz|v38-rosetta|autoresearch-generator> <gpu-id|cpu> <instance> <release-sha256> <source-revision>" >&2
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
  "192.168.99.32:/data1/luanhaoyang/pepagent:v38-boltz:1")
    TASK_QUEUE="pepagent-gpu-boltz2-v38"
    RUN_FAMILY="v38"
    ;;
  "synth:/sdd_data/pepagent:v38-rosetta:cpu")
    TASK_QUEUE="pepagent-cpu-rosetta-v38"
    RUN_FAMILY="v38"
    ;;
  "192.168.99.32:/data1/luanhaoyang/pepagent:autoresearch-generator:1")
    TASK_QUEUE="pepagent-autoresearch-generator-v1"
    RUN_FAMILY="autoresearch-v1"
    ;;
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

GPU_UUID=""
GPU_MEMORY_TOTAL_MIB=""
GPU_MEMORY_FREE_MIB=""
GPU_MEMORY_USED_MIB=""
GPU_UTILIZATION_PERCENT=""
GPU_PREFLIGHT_STATUS="not_applicable_cpu"
assert_gpu_idle() {
  local occupants declaration_process declaration_value declared_device
  occupants="$(nvidia-smi -i "$RESOURCE" --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | tr -d '[:space:]')"
  [[ -z "$occupants" ]] || { echo "GPU has compute processes; refusing launch" >&2; exit 21; }
  GPU_UUID="$(nvidia-smi -i "$RESOURCE" --query-gpu=uuid --format=csv,noheader,nounits | tr -d '[:space:]')"
  [[ "$GPU_UUID" =~ ^GPU-[A-Fa-f0-9-]+$ ]] || { echo "GPU UUID preflight is invalid" >&2; exit 21; }
  for declaration_process in /proc/[0-9]*; do
    declaration_value="$(
      { tr '\0' '\n' < "$declaration_process/environ"; } 2>/dev/null | \
        sed -n 's/^CUDA_VISIBLE_DEVICES=//p' | head -n 1 || true
    )"
    [[ -n "$declaration_value" ]] || continue
    IFS=',' read -r -a declared_devices <<< "$declaration_value"
    for declared_device in "${declared_devices[@]}"; do
      if [[ "$declared_device" = "$RESOURCE" || "$declared_device" = "$GPU_UUID" ]]; then
        echo "GPU has a CUDA_VISIBLE_DEVICES declaration; refusing launch" >&2
        exit 21
      fi
    done
  done
  GPU_MEMORY_TOTAL_MIB="$(nvidia-smi -i "$RESOURCE" --query-gpu=memory.total --format=csv,noheader,nounits | tr -d '[:space:]')"
  GPU_MEMORY_FREE_MIB="$(nvidia-smi -i "$RESOURCE" --query-gpu=memory.free --format=csv,noheader,nounits | tr -d '[:space:]')"
  GPU_UTILIZATION_PERCENT="$(nvidia-smi -i "$RESOURCE" --query-gpu=utilization.gpu --format=csv,noheader,nounits | tr -d '[:space:]')"
  [[ "$GPU_MEMORY_TOTAL_MIB" =~ ^[0-9]+$ && "$GPU_MEMORY_FREE_MIB" =~ ^[0-9]+$ ]] || {
    echo "GPU memory preflight is invalid" >&2
    exit 21
  }
  [[ "$GPU_UTILIZATION_PERCENT" =~ ^[0-9]+$ ]] || { echo "GPU utilization preflight is invalid" >&2; exit 21; }
  GPU_MEMORY_USED_MIB="$((GPU_MEMORY_TOTAL_MIB - GPU_MEMORY_FREE_MIB))"
  if (( GPU_MEMORY_USED_MIB > 256 || GPU_UTILIZATION_PERCENT > 5 )); then
    echo "GPU exceeds the guarded idle threshold; refusing launch" >&2
    exit 21
  fi
  GPU_PREFLIGHT_STATUS="idle_no_compute_process_or_cuda_declaration"
}

BOLTZ_CACHE_ATTESTATION_SHA256=""
MODEL_PATH=""
MODEL_REVISION=""
if [[ "$ROLE" = "v38-boltz" ]]; then
  PYTHON="$ROOT/envs/gpu-worker-py311-v1/bin/python"
  BOLTZ_EXECUTABLE="$ROOT/envs/gpu-worker-py311-v1/bin/boltz"
  BOLTZ_CACHE="$ROOT/models/boltz2/cache"
  CUDA_DEVICE="$RESOURCE"
  MAX_CONCURRENT=1
  [[ -x "$BOLTZ_EXECUTABLE" ]] || { echo "managed Boltz executable is missing" >&2; exit 4; }
  "$PYTHON" -c 'import boltz' || { echo "managed Boltz package is unavailable" >&2; exit 4; }
  BOLTZ_VERSION="$($PYTHON -c 'from importlib.metadata import version; print(version("boltz"))')"
  [[ "$BOLTZ_VERSION" = "2.2.1" ]] || {
    echo "managed Boltz version drifted" >&2
    exit 4
  }
  BOLTZ_GUARDED_SMOKE_SHA256="${PEPAGENT_BOLTZ_GUARDED_SMOKE_SHA256:?guarded Boltz smoke SHA-256 is required}"
  BOLTZ_CACHE_ATTESTATION="$(
    bash "$RELEASE_DIR/deploy/remote/attest_v38_boltz_runtime.sh" \
      "$BOLTZ_CACHE" "$BOLTZ_EXECUTABLE" "$BOLTZ_GUARDED_SMOKE_SHA256"
  )"
  BOLTZ_CACHE_ATTESTATION_SHA256="$(
    printf '%s' "$BOLTZ_CACHE_ATTESTATION" | sha256sum | cut -d ' ' -f 1
  )"
  assert_gpu_idle
elif [[ "$ROLE" = "autoresearch-generator" ]]; then
  PYTHON="$ROOT/envs/gpu-worker-py311-v1/bin/python"
  CUDA_DEVICE="$RESOURCE"
  MAX_CONCURRENT=1
  MODEL_REVISION="898fca941a9057aebdd1a6164b5ee09a1a71780e"
  MODEL_PATH="$ROOT/models/PepMLM-650M/$MODEL_REVISION"
  MODEL_WEIGHTS="$MODEL_PATH/pytorch_model.bin"
  EXPECTED_PEPMLM_WEIGHTS="8a3225bca1f9acd9f701ca2e46597c12bab92320e32b68f380ddf3b6d3b20770"
  [[ -x "$PYTHON" && -f "$MODEL_WEIGHTS" ]] || { echo "managed AutoResearch PepMLM runtime is missing" >&2; exit 4; }
  [[ "$(sha256sum "$MODEL_WEIGHTS" | cut -d ' ' -f 1)" = "$EXPECTED_PEPMLM_WEIGHTS" ]] || {
    echo "AutoResearch PepMLM weights drifted" >&2
    exit 4
  }
  assert_gpu_idle
  env CUDA_VISIBLE_DEVICES="$CUDA_DEVICE" PYTHONPATH="$RELEASE_DIR/src" \
    PEPAGENT_PEPMLM_MODEL_PATH="$MODEL_PATH" \
    PEPAGENT_PEPMLM_MODEL_REVISION="$MODEL_REVISION" \
    PEPAGENT_PEPMLM_WEIGHTS_SHA256="$EXPECTED_PEPMLM_WEIGHTS" \
    "$PYTHON" - <<'PY'
import torch
from pepagent.model_workers import pepmlm_cli  # noqa: F401

if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
    raise SystemExit("AutoResearch generator CUDA placement preflight failed")
torch.cuda.get_device_properties(0)
PY
else
  PYTHON="$ROOT/envs/pyrosetta-quarterly-py311-v1/bin/python"
  CUDA_DEVICE=""
  MAX_CONCURRENT=16
fi
[[ -x "$PYTHON" ]] || { echo "managed worker Python is missing" >&2; exit 4; }

RELEASE_TASK_QUEUE="$(env CUDA_VISIBLE_DEVICES="$CUDA_DEVICE" PYTHONPATH="$RELEASE_DIR/src" \
  PEPAGENT_WORKER_ROLE="$ROLE" "$PYTHON" -c \
  'import os; from pepagent.workers.v38_temporal_worker import V38_ROLE_CONFIG; print(V38_ROLE_CONFIG[os.environ["PEPAGENT_WORKER_ROLE"]][0])')"
[[ "$RELEASE_TASK_QUEUE" = "$TASK_QUEUE" ]] || {
  echo "worker release task queue differs from the launcher contract" >&2
  exit 5
}

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
elif [[ "$ROLE" = "autoresearch-generator" ]]; then
  WEIGHTS_SHA256="$EXPECTED_PEPMLM_WEIGHTS"
else
  WEIGHTS_SHA256=""
fi

RUN_DIR="$ROOT/runs/workers/$RUN_FAMILY/$ROLE/$INSTANCE"
PID_FILE="$RUN_DIR/worker.pid"
WORK_ROOT="$ROOT/work"
if [[ "$ROLE" = "autoresearch-generator" ]]; then
  WORK_ROOT="$ROOT/work-autoresearch-v1"
fi
mkdir -p "$RUN_DIR" "$WORK_ROOT"
if [[ "$ROLE" = "v38-boltz" ]]; then
  mkdir -p "$ROOT/models/boltz2/cache"
fi
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "worker instance is already running; replacement requires external exact-ownership migration" >&2
  exit 20
fi

if [[ "$ROLE" = "v38-boltz" ]]; then
  printf '%s\n' "$BOLTZ_CACHE_ATTESTATION" >"$RUN_DIR/runtime-cache-attestation.json"
fi

if [[ "$RESOURCE" != "cpu" ]]; then
  # Repeat the scoped idle check immediately before launch to close the gap
  # between runtime validation and process creation.  This never enumerates a
  # different GPU index.
  assert_gpu_idle
fi

PYTHON_SHA256="$(sha256sum "$PYTHON" | cut -d ' ' -f 1)"
LAUNCHER_SHA256="$(sha256sum "${BASH_SOURCE[0]}" | cut -d ' ' -f 1)"
[[ "$PYTHON_SHA256" =~ ^[0-9a-f]{64}$ && "$LAUNCHER_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "worker executable identity is invalid" >&2
  exit 5
}

EXTRA_ENV=()
if [[ "$ROLE" = "autoresearch-generator" ]]; then
  EXTRA_ENV+=(
    "PEPAGENT_PEPMLM_MODEL_PATH=$MODEL_PATH"
    "PEPAGENT_PEPMLM_MODEL_REVISION=$MODEL_REVISION"
    "PEPAGENT_PEPMLM_WEIGHTS_SHA256=$WEIGHTS_SHA256"
  )
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
STARTED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
LOG_FILE="$RUN_DIR/worker-$STAMP.log"
PID=""
cleanup_failed_launch() {
  local status="$?" wait_step
  trap - EXIT
  if [[ "$status" -ne 0 && -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    # This PID was created by this exact launcher invocation.  A failed
    # post-launch attestation must not leave an unreceipted poller behind.
    kill "$PID" 2>/dev/null || true
    for wait_step in {1..20}; do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.25
    done
    if kill -0 "$PID" 2>/dev/null; then
      echo "started worker did not exit after bounded cleanup" >&2
    else
      wait "$PID" 2>/dev/null || true
    fi
  fi
  exit "$status"
}
trap cleanup_failed_launch EXIT
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
  PEPAGENT_WORK_ROOT="$WORK_ROOT" \
  PEPAGENT_BOLTZ2_CACHE_PATH="$ROOT/models/boltz2/cache" \
  "${EXTRA_ENV[@]}" \
  "$PYTHON" -m pepagent.workers.v38_temporal_worker \
  >"$LOG_FILE" 2>&1 </dev/null &
PID="$!"
printf '%s\n' "$PID" >"$PID_FILE"
printf '%s\n' "$LOG_FILE" >"$RUN_DIR/latest-log"

sleep 2
if ! kill -0 "$PID" 2>/dev/null; then
  echo "worker exited during guarded launch; inspect $LOG_FILE" >&2
  exit 23
fi
[[ -r "/proc/$PID/environ" ]] || { echo "worker environment cannot be verified" >&2; exit 23; }
for expected_environment in \
  "PEPAGENT_WORKER_ROLE=$ROLE" \
  "PEPAGENT_WORKER_SOURCE_REVISION=$SOURCE_REVISION" \
  "PEPAGENT_PLATFORM_RELEASE_SHA256=$EXPECTED_RELEASE"; do
  tr '\0' '\n' <"/proc/$PID/environ" | grep -Fxq "$expected_environment" || {
    echo "worker process environment differs from its frozen launch contract" >&2
    exit 23
  }
done
if [[ "$RESOURCE" != "cpu" ]]; then
  tr '\0' '\n' <"/proc/$PID/environ" | grep -Fxq "CUDA_VISIBLE_DEVICES=$RESOURCE" || {
    echo "worker GPU placement differs from its frozen launch contract" >&2
    exit 23
  }
fi

RECEIPT_SCHEMA="v38.remote-worker-receipt.1"
if [[ "$ROLE" = "autoresearch-generator" ]]; then
  RECEIPT_SCHEMA="autoresearch.remote-generator-worker-receipt.1"
fi
RECEIPT_FILE="$RUN_DIR/worker.receipt"
RECEIPT_TEMP="$RUN_DIR/.worker.receipt.$PID.tmp"
printf '%s\n' \
  "schema=$RECEIPT_SCHEMA" \
  "ampgent_owned=true" \
  "foreign=false" \
  "role=$ROLE" \
  "instance=$INSTANCE" \
  "task_queue=$TASK_QUEUE" \
  "task_queue_verified_from_release=true" \
  "pid=$PID" \
  "physical_host=$PHYSICAL_HOST" \
  "resource=$RESOURCE" \
  "gpu_uuid=$GPU_UUID" \
  "gpu_preflight=$GPU_PREFLIGHT_STATUS" \
  "gpu_memory_total_mib=$GPU_MEMORY_TOTAL_MIB" \
  "gpu_memory_free_mib=$GPU_MEMORY_FREE_MIB" \
  "gpu_memory_used_mib=$GPU_MEMORY_USED_MIB" \
  "gpu_utilization_percent=$GPU_UTILIZATION_PERCENT" \
  "release_sha256=$EXPECTED_RELEASE" \
  "release_dir=$RELEASE_DIR" \
  "source_revision=$SOURCE_REVISION" \
  "launcher_sha256=$LAUNCHER_SHA256" \
  "python_path=$PYTHON" \
  "python_sha256=$PYTHON_SHA256" \
  "environment_sha256=$ENVIRONMENT_SHA256" \
  "service_tunnel_preflight=passed" \
  "postgresql_endpoint=127.0.0.1:55432" \
  "temporal_endpoint=127.0.0.1:17233" \
  "object_store_endpoint=http://127.0.0.1:19000" \
  "model_path=$MODEL_PATH" \
  "model_revision=$MODEL_REVISION" \
  "weights_sha256=$WEIGHTS_SHA256" \
  "work_root=$WORK_ROOT" \
  "log_file=$LOG_FILE" \
  "worker_command=$PYTHON -m pepagent.workers.v38_temporal_worker" \
  "started_at_utc=$STARTED_AT_UTC" \
  "runtime_cache_attestation_sha256=$BOLTZ_CACHE_ATTESTATION_SHA256" >"$RECEIPT_TEMP"
mv "$RECEIPT_TEMP" "$RECEIPT_FILE"
sha256sum "$RECEIPT_FILE" >"$RUN_DIR/worker.receipt.sha256"
trap - EXIT
echo "started role=$ROLE instance=$INSTANCE resource=$RESOURCE pid=$PID release=$EXPECTED_RELEASE revision=$SOURCE_REVISION environment=$ENVIRONMENT_SHA256"
