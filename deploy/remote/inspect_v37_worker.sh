#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 || ! "$1" =~ ^[1-9][0-9]*$ || ! "$2" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "usage: inspect_v37_worker.sh <pid> <instance>" >&2
  exit 2
fi
PID="$1"
INSTANCE="$2"
[[ -r "/proc/$PID/environ" && -r "/proc/$PID/cmdline" ]] || {
  echo "worker process is absent or unreadable" >&2
  exit 3
}

python3 - "$PID" "$INSTANCE" <<'PY'
import json
import os
import re
import subprocess
import sys

pid = int(sys.argv[1])
instance = sys.argv[2]
environ_raw = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
environment = {}
for item in environ_raw:
    if b"=" in item:
        key, value = item.split(b"=", 1)
        environment[key.decode("utf-8", "strict")] = value.decode("utf-8", "strict")
cmdline = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(
    "utf-8", "strict"
)

required = {
    "PEPAGENT_WORKER_ROLE",
    "PEPAGENT_WORKER_SOURCE_REVISION",
    "PEPAGENT_WORKER_PHYSICAL_HOST",
    "PEPAGENT_WORKER_ROOT",
    "PEPAGENT_WORKER_GPU_INDEX",
    "PEPAGENT_WORKER_ENVIRONMENT_SHA256",
    "PEPAGENT_PLATFORM_RELEASE_SHA256",
}
if not required <= environment.keys():
    raise SystemExit("v37 worker identity environment is incomplete")
role = environment["PEPAGENT_WORKER_ROLE"]
host = environment["PEPAGENT_WORKER_PHYSICAL_HOST"]
resource = environment["PEPAGENT_WORKER_GPU_INDEX"]
root = environment["PEPAGENT_WORKER_ROOT"]
allowed = {
    ("synth", "boltz2", "5"),
    ("synth", "boltz2", "6"),
    ("synth", "rosetta", "cpu"),
    ("192.168.99.19", "boltz2", "5"),
}
if (host, role, resource) not in allowed:
    raise SystemExit("v37 worker placement is outside the frozen allowlist")
expected_root = "/sdd_data/pepagent" if host == "synth" else "/data1/huangyueshan/pepagent"
if root != expected_root:
    raise SystemExit("v37 worker root does not match its physical host")
if not re.fullmatch(r"[0-9a-f]{40}", environment["PEPAGENT_WORKER_SOURCE_REVISION"]):
    raise SystemExit("v37 worker source revision is invalid")
for key in ("PEPAGENT_WORKER_ENVIRONMENT_SHA256", "PEPAGENT_PLATFORM_RELEASE_SHA256"):
    if not re.fullmatch(r"[0-9a-f]{64}", environment[key]):
        raise SystemExit("v37 worker SHA identity is invalid")
if "pepagent.workers.temporal_worker" not in cmdline:
    raise SystemExit("PID is not a PepAgent Temporal worker")
pid_file = f"{root}/runs/workers/v37/{role}/{instance}/worker.pid"
try:
    recorded_pid = int(open(pid_file, encoding="ascii").read().strip())
except (OSError, ValueError) as error:
    raise SystemExit("v37 AMPgent-owned PID receipt is missing or invalid") from error
if recorded_pid != pid:
    raise SystemExit("v37 AMPgent-owned PID receipt does not match the process")

gpu_index = None
foreign = False
if role == "boltz2":
    gpu_index = int(resource)
    output = subprocess.run(
        [
            "nvidia-smi", "-i", resource, "--query-compute-apps=pid",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    occupants = {int(value.strip()) for value in output.splitlines() if value.strip()}
    foreign = bool(occupants - {pid})

queue_by_role = {"boltz2": "pepagent-gpu-boltz2", "rosetta": "pepagent-cpu-rosetta"}
weights = environment.get("PEPAGENT_WORKER_WEIGHTS_SHA256") or None
if role == "boltz2" and not (weights and re.fullmatch(r"[0-9a-f]{64}", weights)):
    raise SystemExit("v37 Boltz worker weights SHA is invalid")
print(json.dumps({
    "physical_host": host,
    "gpu_index": gpu_index,
    "pid": pid,
    "role": role,
    "task_queue": queue_by_role[role],
    "source_revision": environment["PEPAGENT_WORKER_SOURCE_REVISION"],
    "release_sha256": environment["PEPAGENT_PLATFORM_RELEASE_SHA256"],
    "environment_sha256": environment["PEPAGENT_WORKER_ENVIRONMENT_SHA256"],
    "weights_sha256": weights,
    "ampgent_owned": True,
    "foreign_process_present": foreign,
}, sort_keys=True, separators=(",", ":")))
PY
