from pathlib import Path

from pepagent.v37_generator_launch import verify_v37_generator_launch_binding
from pepagent.v37_runtime_manifests import (
    V37GeneratorRuntimeExpectation,
    verify_v37_generator_runtime_manifest,
)
from pepagent.v38_generator_runtime import build_v38_generator_runtime

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
