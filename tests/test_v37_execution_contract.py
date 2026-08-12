from copy import deepcopy
from pathlib import Path

import pytest

from pepagent.v37_evidence import build_v37_evidence_plan
from pepagent.v37_preflight import (
    authorize_v37_submission_preflight,
    bind_v37_submission_inputs,
    build_v37_static_preflight,
)
from pepagent.v37_preregistration import (
    V37Manifest,
    load_v37_preregistration,
    validate_v37_experiment_spec,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_rapid_champion_generation_v37.yaml"


def test_v37_manifest_and_evidence_plan_are_exact() -> None:
    manifest = load_v37_preregistration(CONFIG)
    plan = build_v37_evidence_plan(manifest)
    assert len(plan["generator_calls"]) == 9
    assert len(plan["metric_calls"]) == 5
    assert plan["expected_candidate_count"] == 900
    assert plan["expected_structure_shortlist"] == 48
    assert len(plan["required_tool_call_ids"]) == 21
    hydramp = manifest.generators["engines"][0]
    assert hydramp["source_revision"] == ("36b18003122f0d73323f9644b07e1ed267255c11")
    assert hydramp["upstream_source_revision"] == ("6590d2f4c2963f25d30669052a4c4a857e0e7279")
    assert hydramp["adapter_version"] == "hydramp-safe-pca-stateless-gumbel-v1"
    assert hydramp["formal_seed_acceptance_path"] == (
        "../environments/v37_generator_runtimes/"
        "hydramp.formal-seed-acceptance.json"
    )
    assert hydramp["formal_seed_acceptance_sha256"] == (
        "868905493a3118d2a35ce15ca38144a5c48e347ab31309ed84f2b424353ca8c8"
    )


def test_v37_manifest_rejects_single_generator_drift() -> None:
    manifest = load_v37_preregistration(CONFIG)
    payload = manifest.model_dump(mode="python")
    drifted = deepcopy(payload)
    drifted["generators"]["engines"] = drifted["generators"]["engines"][-1:]
    with pytest.raises(ValueError, match="generator order"):
        V37Manifest.model_validate(drifted)


def _immutable_inputs() -> dict[str, dict[str, object]]:
    return {
        role: {
            "sha256": character * 64,
            "size_bytes": 1,
            "media_type": media_type,
            "storage_uri": f"s3://pepagent/sha256/{character * 2}/{character * 64}",
        }
        for role, character, media_type in (
            ("manifest", "a", "application/yaml"),
            ("experiment_spec", "b", "application/yaml"),
            ("capacity_contract", "c", "application/yaml"),
            ("worker_placement_snapshot", "d", "application/json"),
            ("execution_bundle", "e", "application/json"),
            ("metric_registry", "f", "application/yaml"),
        )
    }


def test_v37_static_and_dynamic_preflight_honors_config_authorization() -> None:
    static = build_v37_static_preflight(CONFIG)
    assert static["direction_authorized"] is True
    assert static["execution_authorized"] is False
    assert static["formal_run_submitted"] is False
    assert static["config_execution_authorized"] is True
    assert static["implementation_revision"] == (
        "a7a0e671fb0234f9365bb083ce40c761cc2d0ccb"
    )
    gates = {
        "implementation_committed_pushed_archived": True,
        "database_schema_exact": True,
        "services_healthy_zero_active_user_workflows": True,
        "provider_releases_exact": True,
        "worker_host_gpu_pid_role_queue_release_exact": True,
        "forbidden_resources_absent": True,
        "no_existing_v37_run_or_workflow": True,
    }
    ready = authorize_v37_submission_preflight(
        static, dynamic_gates=gates, immutable_inputs=_immutable_inputs()
    )
    assert ready["status"] == "ready_to_submit_unique_run"
    assert ready["execution_authorized"] is True
    assert ready["failed_gates"] == []


def test_v37_dynamic_preflight_lists_failed_gate() -> None:
    static = build_v37_static_preflight(CONFIG)
    gates = {
        "implementation_committed_pushed_archived": True,
        "database_schema_exact": False,
        "services_healthy_zero_active_user_workflows": True,
        "provider_releases_exact": True,
        "worker_host_gpu_pid_role_queue_release_exact": True,
        "forbidden_resources_absent": True,
        "no_existing_v37_run_or_workflow": True,
    }
    blocked = authorize_v37_submission_preflight(
        static, dynamic_gates=gates, immutable_inputs=_immutable_inputs()
    )
    assert blocked["status"] == "blocked"
    assert blocked["failed_gates"] == ["database_schema_exact"]


def test_v37_preflight_requires_content_addressed_source_bytes(tmp_path: Path) -> None:
    class Store:
        def put_bytes(self, payload: bytes, media_type: str) -> object:
            digest = __import__("hashlib").sha256(payload).hexdigest()
            return type(
                "Stored",
                (),
                {
                    "sha256": digest,
                    "size_bytes": len(payload),
                    "media_type": media_type,
                    "uri": f"s3://pepagent/sha256/{digest[:2]}/{digest}",
                },
            )()

    paths = []
    for name, payload in (
        ("manifest.yaml", b"m"),
        ("spec.yaml", b"s"),
        ("capacity.yaml", b"c"),
        ("workers.json", b"w"),
        ("run.json", b"e"),
        ("metrics.yaml", b"r"),
    ):
        path = tmp_path / name
        path.write_bytes(payload)
        paths.append(path)
    bindings = bind_v37_submission_inputs(
        manifest_path=paths[0],
        experiment_spec_path=paths[1],
        capacity_contract_path=paths[2],
        worker_placement_snapshot_path=paths[3],
        execution_bundle_path=paths[4],
        metric_registry_path=paths[5],
        object_store=Store(),
    )
    assert set(bindings) == {
        "manifest",
        "experiment_spec",
        "capacity_contract",
        "worker_placement_snapshot",
        "execution_bundle",
        "metric_registry",
    }
    assert all(value["storage_uri"].endswith(str(value["sha256"])) for value in bindings.values())


def test_v37_experiment_spec_is_exact_and_drift_fails_closed(tmp_path: Path) -> None:
    manifest = load_v37_preregistration(CONFIG)
    verified = validate_v37_experiment_spec(manifest, CONFIG)
    assert verified["boltz_seeds"] == [20270380, 20270381, 20270382]
    assert verified["rosetta_decoys_per_pose"] == 16

    source = ROOT / "config" / "experiments" / "acea_v37_rapid_champion_structure.yaml"
    drifted = tmp_path / source.name
    drifted.write_text(
        source.read_text(encoding="utf-8").replace("rosetta_nstruct: 16", "rosetta_nstruct: 8"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="experiment spec SHA drifted"):
        validate_v37_experiment_spec(manifest, CONFIG, spec_path_override=drifted)
