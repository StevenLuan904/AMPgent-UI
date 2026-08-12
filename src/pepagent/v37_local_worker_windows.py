from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

from pepagent.provenance.hashing import sha256_file, sha256_json

PLAN_SCHEMA = "v37.local-windows-worker-plan.1"
RECEIPT_SCHEMA = "v37.local-windows-worker-receipt.1"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
LOCAL_POLLER_IDENTITY = re.compile(
    r"^pepagent:[^:]+:(?P<pid>[1-9][0-9]*)@(?P<host>[^:]+):[0-9a-f]{40}$"
)


@dataclass(frozen=True)
class LocalWorkerRole:
    queue_key: str
    task_queue: str
    worker_module: str
    maximum_concurrent_activities: int
    poller_kinds: tuple[str, ...]
    required_identity_roles: frozenset[str]


COMMON_IDENTITIES = frozenset(
    {"benchmark", "experiment_spec", "execution_bundle", "submission_preflight"}
)
LOCAL_V37_ROLES = {
    "v37-control": LocalWorkerRole(
        queue_key="workflow_and_control",
        task_queue="pepagent-control-v37",
        worker_module="pepagent.workers.v37_temporal_worker",
        maximum_concurrent_activities=16,
        poller_kinds=("workflow", "activity"),
        required_identity_roles=COMMON_IDENTITIES,
    ),
    "v37-generator": LocalWorkerRole(
        queue_key="generator",
        task_queue="pepagent-generator-v37",
        worker_module="pepagent.workers.v37_temporal_worker",
        maximum_concurrent_activities=8,
        poller_kinds=("activity",),
        required_identity_roles=COMMON_IDENTITIES
        | frozenset(
            {
                "generator_runtime_index",
                "hydramp_runtime",
                "ampgan_v2_runtime",
                "amp_designer_runtime",
            }
        ),
    ),
    "v37-provider": LocalWorkerRole(
        queue_key="provider",
        task_queue="pepagent-provider-v37",
        worker_module="pepagent.workers.v37_temporal_worker",
        maximum_concurrent_activities=1,
        poller_kinds=("activity",),
        required_identity_roles=COMMON_IDENTITIES
        | frozenset({"knowledge_runtime", "pepshot_runtime"}),
    ),
    "metrics": LocalWorkerRole(
        queue_key="sequence_metrics",
        task_queue="pepagent-cpu-metrics",
        worker_module="pepagent.workers.temporal_worker",
        maximum_concurrent_activities=5,
        poller_kinds=("activity",),
        required_identity_roles=COMMON_IDENTITIES
        | frozenset(
            {
                "metric_registry",
                "physicochemical_developability_runtime",
                "hemolysis_risk_runtime",
                "mic_potency_runtime",
                "mic_potency_amp_read_runtime",
                "toxicity_risk_runtime",
            }
        ),
    ),
}


class LocalWorkerRefusal(RuntimeError):
    """A fail-closed local worker ownership or identity refusal."""


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalWorkerRefusal(f"{label} is unreadable or invalid JSON") from error
    if not isinstance(payload, dict):
        raise LocalWorkerRefusal(f"{label} must be a JSON object")
    return payload


def _require_sha(value: str, *, length: int, label: str) -> str:
    pattern = HEX40 if length == 40 else HEX64
    if pattern.fullmatch(value) is None:
        raise LocalWorkerRefusal(f"{label} must be a lowercase {length}-hex identity")
    return value


def _canonical_without(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return {item_key: value for item_key, value in payload.items() if item_key != key}


def _runtime_fingerprint(
    *, python_path: Path, release_root: Path, release_sha256: str, source_revision: str
) -> dict[str, Any]:
    script = (
        "import json,pathlib,sys,pepagent;"
        "from pepagent.provenance.environment import fingerprint_runtime;"
        "root=pathlib.Path(sys.argv[1]).resolve();"
        "origin=pathlib.Path(pepagent.__file__).resolve();"
        "assert origin.is_relative_to(root), (origin,root);"
        "digest,manifest=fingerprint_runtime();"
        "print(json.dumps({'environment_sha256':digest,'manifest':manifest,"
        "'pepagent_origin':str(origin),'executable':str(pathlib.Path("
        "getattr(sys,'_base_executable',sys.executable)).resolve()),"
        "'import_paths':[str(pathlib.Path(p).resolve()) for p in sys.path if p and "
        "pathlib.Path(p).resolve()!=root and pathlib.Path(p).exists()]},"
        "sort_keys=True,separators=(',',':')))"
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(release_root / "src"),
            "PEPAGENT_PLATFORM_RELEASE_SHA256": release_sha256,
            "PEPAGENT_WORKER_SOURCE_REVISION": source_revision,
        }
    )
    completed = subprocess.run(
        [str(python_path), "-c", script, str(release_root / "src")],
        cwd=release_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise LocalWorkerRefusal(
            "managed Python cannot import the immutable release: " + completed.stderr[-1000:]
        )
    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as error:
        raise LocalWorkerRefusal("managed Python returned an invalid runtime identity") from error
    if HEX64.fullmatch(str(payload.get("environment_sha256"))) is None:
        raise LocalWorkerRefusal("managed Python environment fingerprint is invalid")
    return payload


def _load_benchmark(path: Path, role: str) -> tuple[dict[str, Any], LocalWorkerRole]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise LocalWorkerRefusal("v37 benchmark is unreadable") from error
    if not isinstance(payload, dict) or payload.get("benchmark_id") != (
        "amp_rapid_champion_generation_v37"
    ):
        raise LocalWorkerRefusal("worker plan requires the v37 rapid-champion benchmark")
    spec = LOCAL_V37_ROLES.get(role)
    if spec is None:
        raise LocalWorkerRefusal(f"local Windows v37 role is not allowed: {role}")
    observed_queue = payload.get("execution", {}).get("task_queues", {}).get(spec.queue_key)
    if observed_queue != spec.task_queue:
        raise LocalWorkerRefusal(
            f"v37 task queue drifted for {role}: {observed_queue!r} != {spec.task_queue!r}"
        )
    formal = payload.get("formal_run", {})
    if formal.get("execution_authorized") is not True or formal.get("submitted") is not False:
        raise LocalWorkerRefusal("v37 formal state does not permit pre-run worker preparation")
    return payload, spec


def build_local_worker_plan(
    *,
    role: str,
    instance: str,
    benchmark_path: Path,
    release_root: Path,
    release_archive_path: Path,
    release_sha256: str,
    source_revision: str,
    python_path: Path,
    state_root: Path,
    identity_paths: dict[str, Path],
    temporal_address: str,
    temporal_namespace: str,
) -> dict[str, Any]:
    """Build a byte-bound plan without launching or contacting Temporal."""

    if platform.system() != "Windows":
        raise LocalWorkerRefusal("the local v37 launcher is Windows-only")
    if re.fullmatch(r"[A-Za-z0-9_-]+", instance) is None:
        raise LocalWorkerRefusal("worker instance contains unsafe characters")
    release_sha256 = _require_sha(release_sha256, length=64, label="release SHA-256")
    source_revision = _require_sha(source_revision, length=40, label="source revision")
    benchmark_path = benchmark_path.resolve(strict=True)
    release_root = release_root.resolve(strict=True)
    release_archive_path = release_archive_path.resolve(strict=True)
    python_path = python_path.resolve(strict=True)
    state_root = state_root.resolve()
    benchmark, spec = _load_benchmark(benchmark_path, role)
    if sha256_file(release_archive_path) != release_sha256:
        raise LocalWorkerRefusal("release archive SHA-256 does not match the requested release")
    revision_marker = release_root / ".pepagent-source-revision"
    release_marker = release_root / ".pepagent-release-sha256"
    if revision_marker.read_text(encoding="ascii").strip() != source_revision:
        raise LocalWorkerRefusal("release source-revision marker drifted")
    if release_marker.read_text(encoding="ascii").strip() != release_sha256:
        raise LocalWorkerRefusal("release SHA marker drifted")
    entrypoint = release_root / "src" / Path(*spec.worker_module.split(".")).with_suffix(".py")
    if not entrypoint.is_file():
        raise LocalWorkerRefusal("worker entrypoint is missing from the immutable release")
    missing = spec.required_identity_roles - set(identity_paths)
    unexpected = set(identity_paths) - spec.required_identity_roles
    if missing or unexpected:
        raise LocalWorkerRefusal(
            f"runtime identity set drifted; missing={sorted(missing)}, "
            f"unexpected={sorted(unexpected)}"
        )
    identities = {
        name: {
            "path": str(path.resolve(strict=True)),
            "sha256": sha256_file(path.resolve(strict=True)),
            "size_bytes": path.resolve(strict=True).stat().st_size,
        }
        for name, path in sorted(identity_paths.items())
    }
    if identities["benchmark"]["sha256"] != sha256_file(benchmark_path):
        raise LocalWorkerRefusal("benchmark identity binding points to another file")
    runtime = _runtime_fingerprint(
        python_path=python_path,
        release_root=release_root,
        release_sha256=release_sha256,
        source_revision=source_revision,
    )
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA,
        "role": role,
        "instance": instance,
        "physical_host": platform.node(),
        "task_queue": spec.task_queue,
        "poller_kinds": list(spec.poller_kinds),
        "worker_module": spec.worker_module,
        "maximum_concurrent_activities": spec.maximum_concurrent_activities,
        "source_revision": source_revision,
        "implementation_revision": benchmark["formal_run"]["implementation_revision"],
        "release_root": str(release_root),
        "release_archive_path": str(release_archive_path),
        "release_sha256": release_sha256,
        "python_path": str(python_path),
        "python_sha256": sha256_file(python_path),
        "managed_python_executable": runtime["executable"],
        "managed_python_executable_sha256": sha256_file(runtime["executable"]),
        "runtime_import_paths": runtime["import_paths"],
        "entrypoint_sha256": sha256_file(entrypoint),
        "runtime_environment_sha256": runtime["environment_sha256"],
        "runtime_manifest": runtime["manifest"],
        "pepagent_origin": runtime["pepagent_origin"],
        "identity_files": identities,
        "state_root": str(state_root),
        "temporal_address": temporal_address,
        "temporal_namespace": temporal_namespace,
        "expected_temporal_identity": (
            f"pepagent:{role}:{{pid}}@{platform.node()}:{source_revision}"
        ),
        "created_at": datetime.now(UTC).isoformat(),
    }
    plan["plan_sha256"] = sha256_json(plan)
    return plan


def validate_local_worker_plan(plan: dict[str, Any], *, rehash_live_files: bool) -> None:
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise LocalWorkerRefusal("local worker plan schema drifted")
    if plan.get("plan_sha256") != sha256_json(_canonical_without(plan, "plan_sha256")):
        raise LocalWorkerRefusal("local worker plan self-hash drifted")
    role = str(plan.get("role"))
    spec = LOCAL_V37_ROLES.get(role)
    if spec is None or plan.get("task_queue") != spec.task_queue:
        raise LocalWorkerRefusal("local worker role or task queue drifted")
    _require_sha(str(plan.get("source_revision")), length=40, label="source revision")
    _require_sha(str(plan.get("release_sha256")), length=64, label="release SHA-256")
    if not rehash_live_files:
        return
    try:
        if sha256_file(plan["release_archive_path"]) != plan["release_sha256"]:
            raise LocalWorkerRefusal("release archive changed after planning")
        if sha256_file(plan["python_path"]) != plan["python_sha256"]:
            raise LocalWorkerRefusal("managed Python changed after planning")
        if (
            sha256_file(plan["managed_python_executable"])
            != plan["managed_python_executable_sha256"]
        ):
            raise LocalWorkerRefusal("managed Python executable changed after planning")
        release_root = Path(plan["release_root"])
        if (
            release_root.joinpath(".pepagent-source-revision")
            .read_text(encoding="ascii")
            .strip()
            != plan["source_revision"]
        ):
            raise LocalWorkerRefusal("release source marker changed after planning")
        if (
            release_root.joinpath(".pepagent-release-sha256")
            .read_text(encoding="ascii")
            .strip()
            != plan["release_sha256"]
        ):
            raise LocalWorkerRefusal("release SHA marker changed after planning")
        spec = LOCAL_V37_ROLES[role]
        entrypoint = release_root / "src" / Path(
            *spec.worker_module.split(".")
        ).with_suffix(".py")
        if sha256_file(entrypoint) != plan["entrypoint_sha256"]:
            raise LocalWorkerRefusal("worker entrypoint changed after planning")
        for name, binding in plan["identity_files"].items():
            path = Path(binding["path"])
            if (
                sha256_file(path) != binding["sha256"]
                or path.stat().st_size != binding["size_bytes"]
            ):
                raise LocalWorkerRefusal(
                    f"runtime identity file changed after planning: {name}"
                )
    except (FileNotFoundError, OSError, KeyError, TypeError) as error:
        raise LocalWorkerRefusal(
            "planned worker identity material is missing or invalid"
        ) from error
    runtime = _runtime_fingerprint(
        python_path=Path(plan["python_path"]),
        release_root=release_root,
        release_sha256=plan["release_sha256"],
        source_revision=plan["source_revision"],
    )
    if runtime["environment_sha256"] != plan["runtime_environment_sha256"]:
        raise LocalWorkerRefusal("managed runtime environment changed after planning")


async def temporal_queue_pollers(plan: dict[str, Any]) -> list[dict[str, str]]:
    client = await Client.connect(
        str(plan["temporal_address"]), namespace=str(plan["temporal_namespace"])
    )
    kinds = {
        "workflow": TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
        "activity": TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
    }
    observed: list[dict[str, str]] = []
    for label in plan["poller_kinds"]:
        response = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=str(plan["temporal_namespace"]),
                task_queue=TaskQueue(name=str(plan["task_queue"])),
                task_queue_type=kinds[label],
            )
        )
        observed.extend(
            {
                "kind": label,
                "identity": poller.identity,
                "last_access_time": poller.last_access_time.ToJsonString(),
            }
            for poller in response.pollers
        )
    return observed


def live_local_queue_pollers(
    pollers: list[dict[str, str]], *, local_host: str
) -> list[dict[str, str]]:
    """Ignore Temporal's historical local pollers only after their exact PID is gone."""

    live: list[dict[str, str]] = []
    local_host = local_host.casefold()
    for poller in pollers:
        match = LOCAL_POLLER_IDENTITY.fullmatch(str(poller.get("identity", "")))
        if match is None or match.group("host").casefold() != local_host:
            live.append(poller)
            continue
        try:
            _powershell_process_snapshot(int(match.group("pid")))
        except LocalWorkerRefusal:
            continue
        live.append(poller)
    return live


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _exclusive_reservation(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise LocalWorkerRefusal(
            "a queue ownership receipt already exists; inspect it instead of launching a duplicate"
        ) from error
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _powershell_process_snapshot(pid: int) -> dict[str, Any]:
    command = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
        "$OutputEncoding=[Console]::OutputEncoding; "
        "$p=Get-CimInstance Win32_Process -Filter \"ProcessId="
        + str(pid)
        + "\"; if($null-eq$p){exit 3}; "
        "$p | Select-Object ProcessId,ExecutablePath,CommandLine,CreationDate "
        "| ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
        )
    except (UnicodeDecodeError, TypeError) as error:
        raise LocalWorkerRefusal("Windows process snapshot was not valid UTF-8") from error
    if completed.returncode != 0:
        raise LocalWorkerRefusal("worker PID is absent or unreadable")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise LocalWorkerRefusal("Windows returned an invalid process snapshot") from error


def inspect_local_worker_receipt(
    receipt_path: Path, *, process_snapshot: dict[str, Any] | None = None
) -> dict[str, Any]:
    receipt = _read_json(receipt_path, label="worker receipt")
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise LocalWorkerRefusal("worker receipt schema drifted")
    if receipt.get("receipt_sha256") != sha256_json(
        _canonical_without(receipt, "receipt_sha256")
    ):
        raise LocalWorkerRefusal("worker receipt self-hash drifted")
    plan = receipt.get("plan")
    if not isinstance(plan, dict):
        raise LocalWorkerRefusal("worker receipt has no embedded immutable plan")
    validate_local_worker_plan(plan, rehash_live_files=True)
    pid = int(receipt["pid"])
    snapshot = process_snapshot or _powershell_process_snapshot(pid)
    if int(snapshot.get("ProcessId", -1)) != pid:
        raise LocalWorkerRefusal("worker PID receipt no longer matches the process")
    if Path(str(snapshot.get("ExecutablePath", ""))).resolve() != Path(
        plan["managed_python_executable"]
    ).resolve():
        raise LocalWorkerRefusal("worker PID is using another Python executable")
    command_line = str(snapshot.get("CommandLine") or "")
    if f"-m {plan['worker_module']}" not in command_line:
        raise LocalWorkerRefusal("worker PID command line is not the planned worker module")
    if str(snapshot.get("CreationDate")) != str(receipt["process_creation_date"]):
        raise LocalWorkerRefusal("worker PID was reused after the receipt was written")
    return {
        "status": "live_exact_worker_process",
        "pid": pid,
        "role": plan["role"],
        "task_queue": plan["task_queue"],
        "source_revision": plan["source_revision"],
        "release_sha256": plan["release_sha256"],
        "runtime_environment_sha256": plan["runtime_environment_sha256"],
        "receipt_sha256": receipt["receipt_sha256"],
    }


def _spawn_worker(
    *,
    plan: dict[str, Any],
    environment: dict[str, str],
    log_path: Path,
) -> subprocess.Popen[bytes]:
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    with log_path.open("ab", buffering=0) as log_handle:
        return subprocess.Popen(
            [str(plan["managed_python_executable"]), "-m", str(plan["worker_module"])],
            cwd=plan["release_root"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
            close_fds=True,
        )


async def launch_local_worker(
    plan_path: Path, *, poller_timeout_seconds: int = 45
) -> dict[str, Any]:
    """Launch exactly one planned Windows worker after all duplicate gates pass."""

    if platform.system() != "Windows":
        raise LocalWorkerRefusal("the local v37 launcher is Windows-only")
    plan = _read_json(plan_path, label="worker plan")
    validate_local_worker_plan(plan, rehash_live_files=True)
    initial_pollers = live_local_queue_pollers(
        await temporal_queue_pollers(plan), local_host=str(plan["physical_host"])
    )
    if initial_pollers:
        raise LocalWorkerRefusal(
            f"task queue already has live pollers: {[item['identity'] for item in initial_pollers]}"
        )
    state_root = Path(plan["state_root"])
    queue_receipt = state_root / "queues" / f"{plan['task_queue']}.json"
    instance_receipt = state_root / "instances" / plan["instance"] / "worker.json"
    if instance_receipt.exists():
        raise LocalWorkerRefusal("worker instance receipt already exists")
    reservation = {
        "schema_version": "v37.local-windows-worker-reservation.1",
        "status": "reserved_before_spawn",
        "plan_sha256": plan["plan_sha256"],
        "role": plan["role"],
        "task_queue": plan["task_queue"],
        "reserved_at": datetime.now(UTC).isoformat(),
    }
    reservation["reservation_sha256"] = sha256_json(reservation)
    _exclusive_reservation(queue_receipt, reservation)

    log_dir = state_root / "instances" / plan["instance"]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "worker.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join(
                [
                    str(Path(plan["release_root"]) / "src"),
                    *[str(item) for item in plan["runtime_import_paths"]],
                ]
            ),
            "PYTHONUNBUFFERED": "1",
            "PEPAGENT_WORKER_ROLE": str(plan["role"]),
            "PEPAGENT_WORKER_SOURCE_REVISION": str(plan["source_revision"]),
            "PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES": str(
                plan["maximum_concurrent_activities"]
            ),
            "PEPAGENT_WORKER_PHYSICAL_HOST": str(plan["physical_host"]),
            "PEPAGENT_WORKER_ENVIRONMENT_SHA256": str(
                plan["runtime_environment_sha256"]
            ),
            "PEPAGENT_PLATFORM_RELEASE_SHA256": str(plan["release_sha256"]),
            "PEPAGENT_TEMPORAL_ADDRESS": str(plan["temporal_address"]),
            "PEPAGENT_TEMPORAL_NAMESPACE": str(plan["temporal_namespace"]),
            "PEPAGENT_WORK_ROOT": str(state_root / "work"),
        }
    )
    metric_registry = plan["identity_files"].get("metric_registry")
    if metric_registry is not None:
        environment["PEPAGENT_METRIC_ADAPTER_REGISTRY_PATH"] = metric_registry["path"]
    process = await asyncio.to_thread(
        _spawn_worker,
        plan=plan,
        environment=environment,
        log_path=log_path,
    )
    deadline = time.monotonic() + poller_timeout_seconds
    expected_identity = str(plan["expected_temporal_identity"]).format(pid=process.pid)
    matched_pollers: list[dict[str, str]] = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LocalWorkerRefusal(
                f"planned worker exited before polling Temporal (code={process.returncode})"
            )
        observed = live_local_queue_pollers(
            await temporal_queue_pollers(plan), local_host=str(plan["physical_host"])
        )
        unexpected = [item for item in observed if item["identity"] != expected_identity]
        if unexpected:
            raise LocalWorkerRefusal("another poller appeared during the launch reservation")
        matched_pollers = [item for item in observed if item["identity"] == expected_identity]
        if {item["kind"] for item in matched_pollers} == set(plan["poller_kinds"]):
            break
        await asyncio.sleep(0.5)
    else:
        raise LocalWorkerRefusal("worker did not acquire every planned Temporal poller in time")
    snapshot = _powershell_process_snapshot(process.pid)
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "status": "live_exact_worker_process",
        "pid": process.pid,
        "process_creation_date": snapshot["CreationDate"],
        "temporal_pollers": matched_pollers,
        "log_path": str(log_path),
        "plan": plan,
        "launched_at": datetime.now(UTC).isoformat(),
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    _atomic_json(instance_receipt, receipt)
    ownership = {
        **reservation,
        "status": "live_exact_worker_process",
        "pid": process.pid,
        "instance_receipt": str(instance_receipt),
        "worker_receipt_sha256": receipt["receipt_sha256"],
    }
    ownership["reservation_sha256"] = sha256_json(
        _canonical_without(ownership, "reservation_sha256")
    )
    _atomic_json(queue_receipt, ownership)
    return receipt


def _parse_identity_bindings(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or name in result:
            raise LocalWorkerRefusal("identity arguments must be unique NAME=PATH pairs")
        result[name] = Path(raw_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed local Windows v37 worker manager")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--role", required=True, choices=sorted(LOCAL_V37_ROLES))
    plan_parser.add_argument("--instance", required=True)
    plan_parser.add_argument("--benchmark", type=Path, required=True)
    plan_parser.add_argument("--release-root", type=Path, required=True)
    plan_parser.add_argument("--release-archive", type=Path, required=True)
    plan_parser.add_argument("--release-sha256", required=True)
    plan_parser.add_argument("--source-revision", required=True)
    plan_parser.add_argument("--python", type=Path, required=True)
    plan_parser.add_argument("--state-root", type=Path, required=True)
    plan_parser.add_argument("--identity", action="append", default=[])
    plan_parser.add_argument("--temporal-address", default="localhost:7233")
    plan_parser.add_argument("--temporal-namespace", default="default")
    plan_parser.add_argument("--output", type=Path, required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--receipt", type=Path, required=True)
    launch_parser = subparsers.add_parser("launch")
    launch_parser.add_argument("--plan", type=Path, required=True)
    launch_parser.add_argument("--poller-timeout-seconds", type=int, default=45)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            plan = build_local_worker_plan(
                role=args.role,
                instance=args.instance,
                benchmark_path=args.benchmark,
                release_root=args.release_root,
                release_archive_path=args.release_archive,
                release_sha256=args.release_sha256,
                source_revision=args.source_revision,
                python_path=args.python,
                state_root=args.state_root,
                identity_paths=_parse_identity_bindings(args.identity),
                temporal_address=args.temporal_address,
                temporal_namespace=args.temporal_namespace,
            )
            _atomic_json(args.output, plan)
            print(json.dumps(plan, sort_keys=True, separators=(",", ":")))
        elif args.command == "inspect":
            print(
                json.dumps(
                    inspect_local_worker_receipt(args.receipt),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            receipt = asyncio.run(
                launch_local_worker(
                    args.plan, poller_timeout_seconds=args.poller_timeout_seconds
                )
            )
            print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    except LocalWorkerRefusal as error:
        parser.exit(2, f"refused: {error}\n")


if __name__ == "__main__":
    main()
