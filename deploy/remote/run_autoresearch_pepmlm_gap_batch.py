from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CANONICAL_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_text(command: list[str]) -> str:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def gpu_snapshot(index: int) -> dict[str, Any]:
    line = run_text(
        [
            "nvidia-smi",
            "-i",
            str(index),
            "--query-gpu=index,uuid,memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    fields = [item.strip() for item in line.split(",")]
    if len(fields) != 4:
        raise RuntimeError(f"unexpected nvidia-smi GPU output: {line!r}")
    compute = subprocess.run(
        [
            "nvidia-smi",
            "-i",
            str(index),
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "index": int(fields[0]),
        "uuid": fields[1],
        "memory_used_mib": int(fields[2]),
        "utilization_percent": int(fields[3]),
        "compute_processes": [row for row in compute.splitlines() if row.strip()],
    }


def own_cuda_declarations(index: int) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    own_pid = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == own_pid:
            continue
        try:
            environ = (entry / "environ").read_bytes().split(b"\0")
            cuda_value = next(
                (
                    item.split(b"=", 1)[1].decode("utf-8", errors="replace")
                    for item in environ
                    if item.startswith(b"CUDA_VISIBLE_DEVICES=")
                ),
                None,
            )
            if cuda_value is None:
                continue
            declared = {token.strip() for token in cuda_value.split(",")}
            if str(index) not in declared:
                continue
            command = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
            )
            declarations.append(
                {"pid": int(entry.name), "cuda_visible_devices": cuda_value, "command": command}
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return declarations


def assert_gpu_idle(snapshot: dict[str, Any], declarations: list[dict[str, Any]]) -> None:
    if snapshot["compute_processes"]:
        raise RuntimeError(f"GPU has compute processes: {snapshot['compute_processes']}")
    if snapshot["memory_used_mib"] > 128 or snapshot["utilization_percent"] > 5:
        raise RuntimeError(f"GPU is not strictly idle: {snapshot}")
    if declarations:
        raise RuntimeError(f"GPU has CUDA_VISIBLE_DEVICES declarations: {declarations}")


def build_actions(
    *, branch_key: str, count: int, seed_base: int, lengths: list[int]
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    temperatures = (0.85, 1.0, 1.15)
    top_ks = (5, 8, 12)
    for ordinal in range(count):
        actions.append(
            {
                "action_id": f"{branch_key}-denovo-{ordinal:04d}",
                "action_kind": "de_novo",
                "seed": seed_base + ordinal,
                "peptide_length": lengths[ordinal % len(lengths)],
                "temperature": temperatures[(ordinal // len(lengths)) % len(temperatures)],
                "top_k": top_ks[(ordinal // (len(lengths) * len(temperatures))) % len(top_ks)],
                "expected_improvement_axes": [
                    "target_conditioning",
                    "amp_activity_model_concordance",
                    "sequence_family_novelty",
                ],
                "protected_axes": [
                    "non_toxin",
                    "low_hemolysis",
                    "instability_index_lt_50",
                ],
            }
        )
    return actions


def validate_output(path: Path, expected: int) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates", [])
    sequences = [str(item.get("sequence", "")) for item in candidates]
    malformed = [item for item in sequences if not item or set(item) - CANONICAL_AA]
    if malformed:
        raise RuntimeError(f"model returned malformed sequences: {malformed[:3]}")
    if len(sequences) != expected:
        raise RuntimeError(f"expected {expected} candidates, found {len(sequences)}")
    return {
        "candidate_count": len(sequences),
        "unique_sequence_count": len(set(sequences)),
        "output_sha256": file_sha256(path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pepmlm-cli", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-weights-sha256", required=True)
    parser.add_argument("--gpu-index", type=int, required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--branches", nargs="+", required=True)
    parser.add_argument("--actions-per-branch", type=int, default=768)
    parser.add_argument("--seed-base", type=int, default=2026082800)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()

    if args.work_root.exists():
        raise RuntimeError(f"exact-once work root already exists: {args.work_root}")
    if file_sha256(args.pepmlm_cli) != args.source_sha256:
        raise RuntimeError("deployed PepMLM CLI hash does not match frozen source hash")
    if file_sha256(args.manifest) != args.manifest_sha256:
        raise RuntimeError("target manifest hash does not match frozen manifest hash")

    before = gpu_snapshot(args.gpu_index)
    declarations = own_cuda_declarations(args.gpu_index)
    if before["uuid"] != args.gpu_uuid:
        raise RuntimeError(f"GPU UUID mismatch: {before['uuid']} != {args.gpu_uuid}")
    assert_gpu_idle(before, declarations)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    targets = {item["target_key"]: item for item in manifest["targets"]}
    unknown = set(args.branches) - set(targets)
    if unknown:
        raise RuntimeError(f"branches absent from target manifest: {sorted(unknown)}")

    lengths = list(range(10, 24))
    workload_spec = {
        "schema_version": "ampgent.autoresearch.pepmlm-gap-batch.v1",
        "branches": args.branches,
        "actions_per_branch": args.actions_per_branch,
        "lengths": lengths,
        "seed_base": args.seed_base,
        "source_sha256": args.source_sha256,
        "target_manifest_sha256": args.manifest_sha256,
        "model_revision": args.model_revision,
        "model_weights_sha256": args.model_weights_sha256,
        "gpu_uuid": args.gpu_uuid,
        "gpu_index": args.gpu_index,
    }
    workload_sha256 = canonical_sha256(workload_spec)
    args.work_root.mkdir(parents=True, exist_ok=False)
    (args.work_root / "requests").mkdir()
    (args.work_root / "outputs").mkdir()
    (args.work_root / "logs").mkdir()
    write_json(args.work_root / "workload_spec.json", workload_spec)
    write_json(
        args.work_root / "launch_receipt.json",
        {
            "schema_version": "ampgent.gpu-launch-receipt.v1",
            "launched_at": utc_now(),
            "pid": os.getpid(),
            "process_start_ticks": Path(f"/proc/{os.getpid()}/stat")
            .read_text(encoding="utf-8")
            .split()[21],
            "workload_sha256": workload_sha256,
            "gpu_preflight": before,
            "cuda_declarations_before_launch": declarations,
            "command": sys.argv,
        },
    )

    status: dict[str, Any] = {
        "workload_sha256": workload_sha256,
        "state": "running",
        "started_at": utc_now(),
        "branches": {},
    }
    write_json(args.work_root / "status.json", status)
    output_receipts: dict[str, Any] = {}
    try:
        for branch_index, branch_key in enumerate(args.branches):
            target = targets[branch_key]
            actions = build_actions(
                branch_key=branch_key,
                count=args.actions_per_branch,
                seed_base=args.seed_base + branch_index * 1_000_000,
                lengths=lengths,
            )
            request = {
                "schema_version": "ampgent.pepmlm.autoresearch-actions.v1",
                "target_key": branch_key,
                "target_sequence": target["sequence"],
                "target_sequence_sha256": target["sequence_sha256"],
                "seed": args.seed_base + branch_index * 1_000_000,
                "model": str(args.model),
                "revision": args.model_revision,
                "top_k": 8,
                "temperature": 1.0,
                "action_plans": actions,
            }
            request_path = args.work_root / "requests" / f"{branch_key}.json"
            output_path = args.work_root / "outputs" / f"{branch_key}.json"
            log_path = args.work_root / "logs" / f"{branch_key}.log"
            write_json(request_path, request)
            status["branches"][branch_key] = {
                "state": "running",
                "started_at": utc_now(),
                "request_sha256": file_sha256(request_path),
            }
            write_json(args.work_root / "status.json", status)
            child_environment = os.environ.copy()
            child_environment.update(
                {
                    "CUDA_VISIBLE_DEVICES": str(args.gpu_index),
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "TOKENIZERS_PARALLELISM": "false",
                    "PYTHONUNBUFFERED": "1",
                }
            )
            with log_path.open("wb") as log_handle:
                subprocess.run(
                    [
                        str(args.python),
                        str(args.pepmlm_cli),
                        "--request",
                        str(request_path),
                        "--output",
                        str(output_path),
                    ],
                    check=True,
                    env=child_environment,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                )
            output_receipts[branch_key] = {
                **validate_output(output_path, args.actions_per_branch),
                "request_sha256": file_sha256(request_path),
                "log_sha256": file_sha256(log_path),
                "completed_at": utc_now(),
            }
            status["branches"][branch_key] = {
                "state": "succeeded",
                **output_receipts[branch_key],
            }
            write_json(args.work_root / "status.json", status)
    except BaseException as error:
        status["state"] = "failed"
        status["failed_at"] = utc_now()
        status["error_type"] = type(error).__name__
        status["error"] = str(error)
        write_json(args.work_root / "status.json", status)
        raise

    status["state"] = "succeeded"
    status["completed_at"] = utc_now()
    write_json(args.work_root / "status.json", status)
    completion = {
        "schema_version": "ampgent.autoresearch.pepmlm-gap-completion.v1",
        "workload_sha256": workload_sha256,
        "completed_at": utc_now(),
        "branch_outputs": output_receipts,
        "total_candidate_count": sum(item["candidate_count"] for item in output_receipts.values()),
        "total_unique_within_branch": sum(
            item["unique_sequence_count"] for item in output_receipts.values()
        ),
    }
    write_json(args.work_root / "completion_receipt.json", completion)


if __name__ == "__main__":
    main()
