import os
import subprocess
import sys
from pathlib import Path

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_generator_launch import verify_v37_generator_launch_binding
from pepagent.v37_runtime_manifests import (
    V37GeneratorRuntimeExpectation,
    verify_v37_generator_runtime_manifest,
)
from pepagent.v38_generator_runtime import (
    build_v38_execution_bundle,
    build_v38_generator_runtime,
)

ROOT = Path(__file__).resolve().parents[1]


def test_v38_runtime_derivation_binds_all_three_real_generator_runtimes() -> None:
    for generator_id in ("hydramp", "ampgan_v2", "amp_designer"):
        manifest, launch = build_v38_generator_runtime(ROOT, generator_id)
        expectation = V37GeneratorRuntimeExpectation(**launch["expectation"])
        verified = verify_v37_generator_runtime_manifest(
            manifest,
            expectation=expectation,
        )
        verify_v37_generator_launch_binding(launch)
        assert verified["verified"] is True
        assert manifest["request_contract"]["properties"]["raw_proposal_budget"] == {
            "const": 100
        }
        assert launch["paths"]["adapter_path"].endswith(
            "amp_designer_generator_v38_cli.py"
            if generator_id == "amp_designer"
            else f"{generator_id}_generator_cli.py"
        )


def test_v38_amp_designer_runtime_does_not_mutate_v37_adapter_identity() -> None:
    manifest, _ = build_v38_generator_runtime(ROOT, "amp_designer")
    assert manifest["adapter"]["adapter_version"] == (
        "amp-designer-v38-score-all-batch100-v1"
    )
    old_adapter = ROOT / "src/pepagent/model_workers/amp_designer_generator_cli.py"
    assert manifest["adapter"]["sha256"] != __import__("hashlib").sha256(
        old_adapter.read_bytes()
    ).hexdigest()


def test_v38_execution_bundle_projects_all_generator_cells_to_100() -> None:
    old = __import__("json").loads(
        (ROOT / "var/run/v37-formal/execution-bundle-v37-015.json").read_text(
            encoding="utf-8"
        )
    )
    projected = build_v38_execution_bundle(ROOT, old)
    for runtime in projected["generator_runtimes"].values():
        assert runtime["request_contract"]["properties"]["raw_proposal_budget"] == {
            "const": 100
        }
    amp_designer = projected["generator_launch_bindings"]["amp_designer"]
    assert amp_designer["paths"]["adapter_path"].endswith(
        "amp_designer_generator_v38_cli.py"
    )
    identity = {
        key: value
        for key, value in projected.items()
        if key != "execution_bundle_identity_sha256"
    }
    assert projected["execution_bundle_identity_sha256"] == sha256_json(identity)


def test_v38_amp_designer_adapter_is_directly_executable_without_package_path(
    tmp_path: Path,
) -> None:
    adapter = ROOT / "src/pepagent/model_workers/amp_designer_generator_v38_cli.py"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, str(adapter), "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--request" in completed.stdout
