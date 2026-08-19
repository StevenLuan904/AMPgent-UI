from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.v38_request_builder import build_v38_request_template
from pepagent.v38_science_execution import build_default_v38_sequence_contract
from pepagent.workflows.v38_sequence_first import _validate_request

SHA = "a" * 64
SOURCE = "b" * 40


def _inputs() -> dict[str, object]:
    contract = build_default_v38_sequence_contract()
    target_runtime: dict[str, dict[str, object]] = {}
    branches = []
    for ordinal, (key, sequence, grade) in enumerate(
        (("target-a", "ACDEFGHIKLMN", "A"), ("target-b", "RSTVWYACDEFG", "B"))
    ):
        target_id = uuid4()
        native_id = uuid4()
        wrong_id = uuid4()
        raw = {
            "target_key": key,
            "target_id": str(target_id),
            "target_sequence_sha256": sha256_text(sequence),
            "coordinate_source_accession": f"TEST{ordinal}",
            "coordinate_source_uri": f"https://example.invalid/TEST{ordinal}.cif",
            "coordinate_sha256": sha256_json({"coordinate": ordinal}),
            "coordinate_size_bytes": 100 + ordinal,
            "coordinate_model_count": 1,
            "coordinate_atom_count": 10 + ordinal,
            "primary_pocket_id": str(native_id),
            "primary_pocket_definition_sha256": sha256_json({"native": ordinal}),
            "primary_pocket_grade": grade,
            "primary_evidence_sha256": [sha256_json({"evidence": ordinal})],
            "wrong_pocket_id": str(wrong_id),
            "wrong_pocket_definition_sha256": sha256_json({"wrong": ordinal}),
            "wrong_pocket_grade": grade,
            "wrong_evidence_sha256": [sha256_json({"control": ordinal})],
            "selected_before_peptide_outcomes": True,
            "peptide_or_structure_outcomes_used_for_selection": False,
            "target_agnostic_amp_lane_retained": True,
        }
        branches.append(raw)
        target_runtime[str(target_id)] = {
            "target_sequence": sequence,
            "pockets_by_id": {
                str(native_id): [1, 2, 3],
                str(wrong_id): [8, 9, 10],
            },
        }
    engines = []
    for name in ("hydramp", "ampgan_v2", "amp_designer"):
        engines.append(
            {
                "generator_id": name,
                "seeds": [
                    cell.seed for cell in contract.cells if cell.generator_id == name
                ],
            }
        )
    metrics = {name: {"runtime_id": name} for name in contract.metric_plugins}
    provider_release = {
        "release_revision": "provider-release",
        "runtime_manifest_sha256": SHA,
    }
    return {
        "benchmark": {
            "benchmark_id": "amp_sequence_first_multitarget_v38",
            "scope": {
                "formal_run_authorized": True,
                "formal_run_submitted": False,
            },
            "knowledge_use": {
                "provider_task_id": "provider-task",
                "provider_smoke_context_pack_sha256": SHA,
                "refinement_provider": {
                    "activity_name": "refine_v38_sequences_with_knowledge",
                    "task_queue": "pepagent-refinement-provider-v38",
                    **provider_release,
                },
            },
            "multitarget_parallelism": {
                "existing_qualification_contract": "qualified-panel-v1"
            },
        },
        "panel": {
            "schema_version": "v38.target-panel.1",
            "selection_frozen_before_peptide_outcomes": True,
            "branches": branches,
            "science_budget_per_branch": {
                "maximum_structure_candidates": 48,
                "boltz_seeds_per_candidate": 3,
                "rosetta_decoys_per_pose": 16,
            },
        },
        "controller_state": {
            "schema_version": "v38.agent-controller-state.1",
            "formal_science_workflow_submitted": False,
            "blockers": [],
            "history_snapshot_sha256": SHA,
            "refinement_provider_release": provider_release,
        },
        "worker_placement": {
            "workers": {"v38-control": {"source_revision": SOURCE}}
        },
        "generator_manifest": {"generators": {"engines": engines}},
        "execution_bundle": {
            "schema_version": "v37.execution-bundle.1",
            "generator_runtimes": {name: {"generator_id": name} for name in (
                "hydramp", "ampgan_v2", "amp_designer"
            )},
            "generator_launch_bindings": {name: {"generator_id": name} for name in (
                "hydramp", "ampgan_v2", "amp_designer"
            )},
            "metric_plugins_by_name": metrics,
        },
        "structure_spec": {"schema_version": "test.structure.1"},
        "target_runtime_by_id": target_runtime,
        "control_environment_sha256": SHA,
    }


def test_request_builder_produces_complete_workflow_input() -> None:
    request = build_v38_request_template(**_inputs())  # type: ignore[arg-type]
    assert request.get("run_id") is None
    assert request.get("submission_preflight") is None
    assert len(request["execution_contract"]["cells"]) == 9
    assert set(request["structure_runtime_by_target_key"]) == {"target-a", "target-b"}
    validated = deepcopy(request)
    validated["submission_preflight"] = {"status": "ready_to_submit_unique_run"}
    _validate_request(validated)


def test_request_builder_rejects_target_sequence_drift() -> None:
    inputs = _inputs()
    runtimes = inputs["target_runtime_by_id"]
    assert isinstance(runtimes, dict)
    next(iter(runtimes.values()))["target_sequence"] = "AAAAAAAAAAAA"
    with pytest.raises(ValueError, match="target sequence drifted"):
        build_v38_request_template(**inputs)  # type: ignore[arg-type]
