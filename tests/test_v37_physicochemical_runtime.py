from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from pepagent.handoff_metrics import physicochemical_descriptors
from pepagent.provenance.hashing import sha256_json
from pepagent.v37_runtime_descriptor_cli import (
    freeze_v37_generic_runtime_descriptor,
)
from pepagent.v37_runtime_execution import (
    V37GenericRuntimeExpectation,
    V37GenericRuntimePaths,
    run_v37_guarded_provider_subprocess,
)
from pepagent.v37_submit_cli import _validate_generic_execution_guard

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ID = "physicochemical-developability-modlamp-4.3.2-v37"
ADAPTER = (
    REPO_ROOT
    / "src"
    / "pepagent"
    / "model_workers"
    / "physicochemical_runtime"
    / "cli.py"
)
SOURCE_ROOT = ADAPTER.parent
MODEL_ROOT = (
    REPO_ROOT
    / "config"
    / "environments"
    / "v37_metric_runtimes"
    / "physicochemical_model_release"
)
RUNTIME_MANIFEST = (
    REPO_ROOT
    / "config"
    / "metrics"
    / "manifests"
    / "physicochemical_developability_modlamp_4_3_2_v37.json"
)
PACKAGES_LOCK = REPO_ROOT / "uv.lock"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _freeze_descriptor(tmp_path: Path) -> dict[str, object]:
    base_path = tmp_path / "base-runtime.json"
    base_path.write_text(
        json.dumps(
            {
                "schema_version": "v37.metric-runtime-descriptor.1",
                "runtime_id": RUNTIME_ID,
                "name": "physicochemical_developability",
                "plugin_name": "physicochemical_developability",
                "provider": "builtin",
                "python_path": sys.executable,
                "adapter_path": str(ADAPTER),
                "cwd": str(REPO_ROOT),
                "runtime_manifest_path": str(RUNTIME_MANIFEST),
                "runtime_manifest_sha256": _sha256_file(RUNTIME_MANIFEST),
                "packages_lock_path": str(PACKAGES_LOCK),
                "source_root": str(SOURCE_ROOT),
                "model_root": str(MODEL_ROOT),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return freeze_v37_generic_runtime_descriptor(
        base_runtime_path=base_path,
        runtime_id=RUNTIME_ID,
        executable_path=Path(sys.executable),
        adapter_path=ADAPTER,
        runtime_manifest_path=RUNTIME_MANIFEST,
        packages_lock_path=PACKAGES_LOCK,
        source_root=SOURCE_ROOT,
        model_root=MODEL_ROOT,
        cwd=REPO_ROOT,
        executable_index=0,
        adapter_index=1,
    )


@pytest.mark.asyncio
async def test_physicochemical_runtime_is_guarded_and_matches_pinned_protocol(
    tmp_path: Path,
) -> None:
    descriptor = _freeze_descriptor(tmp_path)
    guard = descriptor["execution_guard"]
    assert isinstance(guard, dict)
    contract = guard["contract"]
    expectation = V37GenericRuntimeExpectation(**guard["expectation"])
    paths = V37GenericRuntimePaths(
        executable_path=Path(sys.executable),
        adapter_path=ADAPTER,
        runtime_manifest_path=RUNTIME_MANIFEST,
        packages_lock_path=PACKAGES_LOCK,
        source_root=SOURCE_ROOT,
        model_root=MODEL_ROOT,
    )
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "result.json"
    request = {
        "run_id": "test-v37-physicochemical",
        "plugin": {
            "name": "physicochemical_developability",
            "parameters": {
                "ph": 7.4,
                "c_terminal_amidated": False,
                "hydrophobic_moment_angle": 100,
            },
        },
        "candidates": [
            {"id": "candidate-1", "sequence": "KWKLFKKIEKVGQNIRDGIIKAGPAVAVVGQATQIAK"},
            {"id": "candidate-2", "sequence": "GIGKFLHSAKKFGKAFVGEIMNS"},
        ],
    }
    request_path.write_text(json.dumps(request), encoding="utf-8")
    receipts: list[dict[str, object]] = []

    async def record_receipt(receipt: dict[str, object]) -> None:
        receipts.append(receipt)

    output, aggregate = await run_v37_guarded_provider_subprocess(
        [
            sys.executable,
            str(ADAPTER),
            "--request",
            str(request_path),
            "--output",
            str(output_path),
        ],
        contract=contract,
        expectation=expectation,
        paths=paths,
        receipt_writer=record_receipt,
        cwd=REPO_ROOT,
        env=os.environ,
        input_paths={"request": request_path},
    )

    assert json.loads(output)["status"] == "complete"
    assert len(receipts) == 1
    assert receipts[0]["stage"] == "pre_snapshot"
    assert aggregate["all_boundaries_match"] is True
    assert aggregate["returncode"] == 0
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["runtime_id"] == RUNTIME_ID
    assert result["candidate_count"] == 2
    assert {
        observation["metric_name"]
        for observation in result["records"][0]["observations"]
    } == {
        "hydrophobic_moment_eisenberg",
        "hydrophobic_ratio_modlamp",
        "maximum_hydrophobic_run",
        "net_charge_ph7_4",
    }
    for candidate, row in zip(request["candidates"], result["raw_rows"], strict=True):
        expected = physicochemical_descriptors(candidate["sequence"])
        for field in (
            "molecular_weight",
            "net_charge_ph7_4",
            "isoelectric_point",
            "hydrophobic_ratio",
            "hydrophobic_moment",
        ):
            assert row[field] == pytest.approx(expected[field], rel=1e-12, abs=1e-12)
    assert [row["maximum_hydrophobic_run"] for row in result["raw_rows"]] == [5, 3]


def test_v37_activity_uses_guarded_runtime_for_builtin_descriptors() -> None:
    activity_source = (
        REPO_ROOT / "src" / "pepagent" / "workers" / "v37_activities.py"
    ).read_text(encoding="utf-8")
    assert "builtin_no_subprocess" not in activity_source
    assert "physicochemical_descriptors(" not in activity_source
    assert '"execution_mode": "guarded_subprocess"' in activity_source


def test_physicochemical_runtime_descriptor_is_self_hashed_and_has_no_hidden_model(
    tmp_path: Path,
) -> None:
    descriptor = _freeze_descriptor(tmp_path)
    identity = descriptor.pop("runtime_identity_sha256")
    assert identity == sha256_json(descriptor)
    assert descriptor["name"] == "physicochemical_developability"
    contract = descriptor["execution_guard"]["contract"]
    assert [item["path"] for item in contract["source_release"]["files"]] == [
        "__init__.py",
        "cli.py",
    ]
    assert [item["path"] for item in contract["model_release"]["files"]] == [
        "manifest.json"
    ]


def test_physicochemical_runtime_passes_submission_guard_preflight(tmp_path: Path) -> None:
    descriptor = _freeze_descriptor(tmp_path)
    _validate_generic_execution_guard(
        descriptor,
        label="metric physicochemical_developability",
    )
    descriptor["execution_guard"]["contract"]["adapter"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="self-hash drifted"):
        _validate_generic_execution_guard(
            descriptor,
            label="metric physicochemical_developability",
        )
