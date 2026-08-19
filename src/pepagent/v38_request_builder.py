from __future__ import annotations

from copy import deepcopy
from typing import Any

from pepagent.provenance.hashing import sha256_text
from pepagent.v38_science_execution import build_default_v38_sequence_contract
from pepagent.v38_sequence_first_multitarget import (
    TargetBranchSpec,
    TargetQualificationWitness,
)


def _require_mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"v38 {label} must be an object")
    return value


def _require_sha(value: object, *, label: str, length: int = 64) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"v38 {label} is not a SHA identity")
    return value


def build_v38_request_template(
    *,
    benchmark: dict[str, Any],
    panel: dict[str, Any],
    controller_state: dict[str, Any],
    worker_placement: dict[str, Any],
    generator_manifest: dict[str, Any],
    execution_bundle: dict[str, Any],
    structure_spec: dict[str, Any],
    target_runtime_by_id: dict[str, dict[str, Any]],
    control_environment_sha256: str,
) -> dict[str, Any]:
    """Build the immutable pre-reservation input to the v38 workflow.

    Historical v37 runtime descriptors are executable release metadata, not
    scientific outputs.  They remain byte-bound here while all candidates and
    evaluations are generated afresh by the new run.
    """

    if benchmark.get("benchmark_id") != "amp_sequence_first_multitarget_v38":
        raise ValueError("v38 benchmark identity drifted")
    scope = _require_mapping(benchmark.get("scope"), label="benchmark scope")
    if scope.get("formal_run_authorized") is not True or scope.get(
        "formal_run_submitted"
    ) is not False:
        raise ValueError("v38 benchmark is not authorized and unsubmitted")
    if controller_state.get("schema_version") != "v38.agent-controller-state.1":
        raise ValueError("v38 controller state schema drifted")
    if controller_state.get("formal_science_workflow_submitted") is not False:
        raise ValueError("v38 controller already records a formal submission")
    if controller_state.get("blockers") != []:
        raise ValueError("v38 controller still has blockers")
    if panel.get("schema_version") != "v38.target-panel.1" or panel.get(
        "selection_frozen_before_peptide_outcomes"
    ) is not True:
        raise ValueError("v38 target panel is not frozen before outcomes")
    if execution_bundle.get("schema_version") != "v37.execution-bundle.1":
        raise ValueError("v38 executable runtime bundle schema drifted")

    contract = build_default_v38_sequence_contract()
    engines = _require_mapping(
        generator_manifest.get("generators"), label="generator manifest"
    ).get("engines")
    if not isinstance(engines, list):
        raise ValueError("v38 generator engine manifest is absent")
    engines_by_name = {str(item.get("generator_id")): item for item in engines}
    generator_names = {cell.generator_id for cell in contract.cells}
    if set(engines_by_name) != generator_names:
        raise ValueError("v38 generator engine set drifted")
    frozen_seeds = {
        name: tuple(int(seed) for seed in engine.get("seeds", []))
        for name, engine in engines_by_name.items()
    }
    contract_seeds = {
        name: tuple(cell.seed for cell in contract.cells if cell.generator_id == name)
        for name in generator_names
    }
    if frozen_seeds != contract_seeds:
        raise ValueError("v38 generator seeds differ from the frozen contract")

    runtimes = _require_mapping(
        execution_bundle.get("generator_runtimes"), label="generator runtimes"
    )
    bindings = _require_mapping(
        execution_bundle.get("generator_launch_bindings"),
        label="generator launch bindings",
    )
    metrics = _require_mapping(
        execution_bundle.get("metric_plugins_by_name"), label="metric plugins"
    )
    if set(runtimes) != generator_names or set(bindings) != generator_names:
        raise ValueError("v38 generator runtime coverage drifted")
    if set(metrics) != set(contract.metric_plugins):
        raise ValueError("v38 score-all metric runtime coverage drifted")

    branches_payload = panel.get("branches")
    if not isinstance(branches_payload, list) or not 2 <= len(branches_payload) <= 6:
        raise ValueError("v38 target panel requires two to six branches")
    budget = _require_mapping(
        panel.get("science_budget_per_branch"), label="target science budget"
    )
    branches: list[dict[str, Any]] = []
    structure_runtimes: dict[str, dict[str, Any]] = {}
    for raw in branches_payload:
        witness = TargetQualificationWitness.model_validate(raw)
        runtime = target_runtime_by_id.get(str(witness.target_id))
        if not isinstance(runtime, dict):
            raise ValueError(f"v38 target runtime is missing: {witness.target_key}")
        sequence = str(runtime.get("target_sequence", ""))
        if sha256_text(sequence) != witness.target_sequence_sha256:
            raise ValueError(f"v38 target sequence drifted: {witness.target_key}")
        pockets = _require_mapping(runtime.get("pockets_by_id"), label="target pockets")
        native = pockets.get(str(witness.primary_pocket_id))
        wrong = pockets.get(str(witness.wrong_pocket_id))
        if not isinstance(native, list) or not isinstance(wrong, list) or not native or not wrong:
            raise ValueError(f"v38 target pocket residues are incomplete: {witness.target_key}")
        branch = TargetBranchSpec(
            target_key=witness.target_key,
            target_id=witness.target_id,
            target_sequence_sha256=witness.target_sequence_sha256,
            coordinate_sha256=witness.coordinate_sha256,
            native_pocket_sha256=witness.primary_pocket_definition_sha256,
            wrong_pocket_sha256=witness.wrong_pocket_definition_sha256,
            qualification_witness_sha256=witness.sha256(),
            evidence_grade=witness.primary_pocket_grade,
            panel_role="qualified_target",
            structure_budget=int(budget["maximum_structure_candidates"]),
            boltz_seeds_per_candidate=int(budget["boltz_seeds_per_candidate"]),
            rosetta_decoys_per_pose=int(budget["rosetta_decoys_per_pose"]),
        )
        branches.append(branch.model_dump(mode="json"))
        structure_runtimes[witness.target_key] = {
            "target_sequence": sequence,
            "pocket_residues_by_lane": {
                "native": [int(item) for item in native],
                "wrong_pocket": [int(item) for item in wrong],
            },
            "structure_spec": deepcopy(structure_spec),
        }

    provider = _require_mapping(
        benchmark.get("knowledge_use"), label="knowledge use"
    ).get("refinement_provider")
    provider = _require_mapping(provider, label="refinement provider")
    accepted = _require_mapping(
        controller_state.get("refinement_provider_release"),
        label="accepted refinement provider",
    )
    if provider.get("release_revision") != accepted.get("release_revision") or provider.get(
        "runtime_manifest_sha256"
    ) != accepted.get("runtime_manifest_sha256"):
        raise ValueError("v38 refinement provider acceptance drifted")
    provider_request = {
        "activity_name": provider["activity_name"],
        "task_queue": provider["task_queue"],
        "provider_task_id": benchmark["knowledge_use"]["provider_task_id"],
        "release_revision": provider["release_revision"],
        "runtime_manifest_sha256": provider["runtime_manifest_sha256"],
    }

    source = worker_placement["workers"]["v38-control"]["source_revision"]
    _require_sha(source, label="worker source", length=40)
    request = {
        "execution_contract": contract.model_dump(mode="json"),
        "generator_engines_by_name": deepcopy(engines_by_name),
        "generator_runtimes_by_name": deepcopy(runtimes),
        "generator_launch_bindings_by_name": deepcopy(bindings),
        "metric_plugins_by_name": deepcopy(metrics),
        "refinement_provider": provider_request,
        "knowledge_context_pack_sha256": _require_sha(
            benchmark["knowledge_use"]["provider_smoke_context_pack_sha256"],
            label="knowledge context pack",
        ),
        "multitarget_plan_template": {
            "harness_release_id": str(
                benchmark["multitarget_parallelism"]["existing_qualification_contract"]
            ),
            "history_snapshot_sha256": _require_sha(
                controller_state["history_snapshot_sha256"], label="history snapshot"
            ),
            "target_branches": branches,
            "max_parallel_targets": len(branches),
        },
        "structure_runtime_by_target_key": structure_runtimes,
        "boltz_seeds": [20270380, 20270381, 20270382],
        "task_queues": {
            "workflow_and_control": "pepagent-control-v38",
            "generator": "pepagent-generator-v38",
            "sequence_metrics": "pepagent-cpu-metrics-v38",
            "structure_boltz": "pepagent-gpu-boltz2-v38",
            "structure_rosetta": "pepagent-cpu-rosetta-v38",
        },
        "generation_concurrency": 3,
        "metric_concurrency": 5,
        "structure_concurrency": 2,
        "control_environment_sha256": _require_sha(
            control_environment_sha256, label="control environment"
        ),
        "worker_source_revision": source,
    }
    return request
