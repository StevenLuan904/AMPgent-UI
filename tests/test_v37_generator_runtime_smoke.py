from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.v37_generator_launch import build_v37_generator_launch_binding
from pepagent.v37_persistence import _validate_generator_launch_binding_evidence
from pepagent.v37_runtime_execution import V37LiveRuntimePaths, run_v37_guarded_subprocess
from pepagent.v37_runtime_manifests import V37GeneratorRuntimeExpectation
from pepagent.workers.v37_activities import (
    _generator_command,
    _materialize_hydramp_models,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "config/environments/v37_generator_runtimes"


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _peak_working_set(process: subprocess.Popen[str]) -> int:
    if os.name != "nt":
        return 0
    counters = _ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
        handle, ctypes.byref(counters), counters.cb
    )
    return int(counters.PeakWorkingSetSize) if ok else 0


def _run(command: list[str], *, env: dict[str, str], timeout: float) -> tuple[str, float, int]:
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    peak = 0
    while process.poll() is None:
        peak = max(peak, _peak_working_set(process))
        if time.monotonic() - started > timeout:
            process.kill()
            raise TimeoutError(f"generator smoke timed out: {command[1]}")
        time.sleep(0.2)
    stdout = process.stdout.read() if process.stdout is not None else ""
    elapsed = time.monotonic() - started
    if process.returncode != 0:
        raise RuntimeError(f"generator smoke failed ({process.returncode}):\n{stdout[-8000:]}")
    return stdout, elapsed, peak


@pytest.mark.skipif(
    os.environ.get("PEPAGENT_RUN_V37_GENERATOR_SMOKE") != "1",
    reason="explicit real-runtime smoke only",
)
def test_actual_three_generator_runtime_smoke(tmp_path: Path) -> None:
    index = json.loads((RUNTIME_ROOT / "runtime-index.json").read_text(encoding="utf-8"))
    receipts = []
    budgets = {"hydramp": 2, "ampgan_v2": 2, "amp_designer": 1000}
    for entry in index["entries"]:
        generator_id = entry["generator_id"]
        selected = os.environ.get("PEPAGENT_V37_SMOKE_GENERATOR")
        if selected and generator_id != selected:
            continue
        manifest = json.loads((ROOT / entry["manifest_path"]).read_text(encoding="utf-8"))
        binding = build_v37_generator_launch_binding(
            workspace=ROOT,
            runtime_index=index,
            entry=entry,
            manifest=manifest,
        )
        work = tmp_path / generator_id
        work.mkdir()
        request_path = work / "request.json"
        output_path = work / "output.json"
        request = {
            "generator_id": generator_id,
            "seed": 12345,
            "raw_proposal_budget": budgets[generator_id],
        }
        if generator_id == "amp_designer":
            request.update(
                batch_size=100,
                batches=10,
                top_k=10,
                top_p=1.0,
                temperature=None,
                decode_steps=34,
                device="cpu",
            )
        request_path.write_text(json.dumps(request), encoding="utf-8")
        materialized = (
            _materialize_hydramp_models(binding, work)
            if generator_id == "hydramp"
            else None
        )
        model_path = materialized[0] if materialized else None
        command = _generator_command(
            {"generator_id": generator_id},
            binding,
            request_path,
            output_path,
            hydramp_model_path=model_path,
        )
        env = dict(os.environ)
        source_root = binding["paths"]["source_root"]
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (source_root, str(ROOT / "src")) if value
        )
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        stdout, elapsed, peak = _run(command, env=env, timeout=1800.0)
        result = json.loads(output_path.read_text(encoding="utf-8"))
        sequences = [item["sequence"] for item in result["records"]]
        assert len(sequences) == budgets[generator_id]
        receipt = {
                "generator_id": generator_id,
                "rows": len(sequences),
                "ordered_output_sha256": sha256_json(sequences),
                "elapsed_seconds": elapsed,
                "peak_working_set_bytes": peak,
                "stdout_tail": stdout[-1000:],
                "error": None,
            }
        receipts.append(receipt)
        print(json.dumps(receipt, sort_keys=True), flush=True)
    assert receipts, "no generator matched PEPAGENT_V37_SMOKE_GENERATOR"
    print(json.dumps(receipts, indent=2, sort_keys=True))


@pytest.mark.skipif(
    os.environ.get("PEPAGENT_RUN_V37_GENERATOR_SMOKE") != "1",
    reason="explicit real-runtime smoke only",
)
def test_real_hydramp_materialize_guard_launch_persist_replay(tmp_path: Path) -> None:
    index = json.loads((RUNTIME_ROOT / "runtime-index.json").read_text(encoding="utf-8"))
    entry = next(item for item in index["entries"] if item["generator_id"] == "hydramp")
    manifest = json.loads((ROOT / entry["manifest_path"]).read_text(encoding="utf-8"))
    binding = build_v37_generator_launch_binding(
        workspace=ROOT, runtime_index=index, entry=entry, manifest=manifest
    )
    work = tmp_path / "hydramp"
    work.mkdir()
    request_path = work / "request.json"
    output_path = work / "output.json"
    request_path.write_text(
        json.dumps(
            {"generator_id": "hydramp", "seed": 12345, "raw_proposal_budget": 1}
        ),
        encoding="utf-8",
    )
    model_path, materialization_receipt = _materialize_hydramp_models(binding, work)
    command = _generator_command(
        {"generator_id": "hydramp"},
        binding,
        request_path,
        output_path,
        hydramp_model_path=model_path,
    )
    paths = binding["paths"]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((paths["source_root"], str(ROOT / "src")))
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    async def execute() -> dict[str, object]:
        async def ignore(_receipt: dict[str, object]) -> None:
            return None

        _stdout, receipt = await run_v37_guarded_subprocess(
            command,
            manifest=manifest,
            expectation=V37GeneratorRuntimeExpectation(**entry["expectation"]),
            paths=V37LiveRuntimePaths(
                adapter_path=Path(paths["adapter_path"]),
                python_path=Path(paths["python_path"]),
                packages_lock_path=Path(paths["packages_lock_path"]),
                source_root=Path(paths["source_root"]),
                model_root=Path(paths["model_root"]),
            ),
            receipt_writer=ignore,
            aggregate_receipt_writer=ignore,
            cwd=work,
            env=env,
        )
        return receipt

    launch_receipt = asyncio.run(execute())
    payload = {
        "live_launch_receipt": launch_receipt,
        "materialization_receipt": materialization_receipt,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = sha256_bytes(raw)
    graph = {
        "tool_calls": [
            {
                "id": "call-1",
                "tool_name": "v37-generate-hydramp",
                "input_json": {"generator": "hydramp"},
            }
        ],
        "artifacts": [{"id": "artifact-1", "sha256": digest}],
        "evidence_artifacts": [
            {
                "tool_call_id": "call-1",
                "artifact_id": "artifact-1",
                "role": "source_runtime_receipt",
            }
        ],
    }
    _validate_generator_launch_binding_evidence(
        graph=graph,
        artifact_bytes_by_sha256={digest: raw},
        execution={
            "generator_launch_bindings": {"hydramp": binding},
            "generator_runtimes": {"hydramp": manifest},
        },
    )
    assert len(json.loads(output_path.read_text(encoding="utf-8"))["records"]) == 1
